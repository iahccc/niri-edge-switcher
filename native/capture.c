#define _GNU_SOURCE

#include <errno.h>
#include <poll.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#include <pixman.h>
#include <wayland-client.h>

#include "ext-foreign-toplevel-list-v1.h"
#include "ext-image-capture-source-v1.h"
#include "ext-image-copy-capture-v1.h"

enum {
    FRAME_INTERVAL_MS = 100,
    MAX_BUFFER_BYTES = 256 * 1024 * 1024,
    UNAVAILABLE = 2,
};

struct constraints {
    uint32_t width, height;
    bool argb, xrgb;
};

struct buffer {
    struct wl_buffer *proxy;
    uint32_t *pixels;
    int32_t width, height, stride, size;
    uint32_t format;
    bool initialized;
};

struct capture {
    const char *identifier;
    uint16_t max_width, max_height;
    struct wl_display *display;
    struct wl_shm *shm;
    struct ext_foreign_toplevel_list_v1 *list;
    struct ext_foreign_toplevel_handle_v1 *window;
    struct ext_foreign_toplevel_image_capture_source_manager_v1 *sources;
    struct ext_image_copy_capture_manager_v1 *manager;
    struct ext_image_copy_capture_session_v1 *session;
    struct ext_image_copy_capture_frame_v1 *frame;
    struct constraints pending, constraints;
    struct buffer buffer;
    int64_t next_frame_ms;
    uint32_t transform;
    bool retrying_buffer;
    bool stopped;
};

static int64_t monotonic_ms(void)
{
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static void destroy_buffer(struct buffer *buffer)
{
    if (!buffer->proxy)
        return;

    wl_buffer_destroy(buffer->proxy);
    munmap(buffer->pixels, buffer->size);
    *buffer = (struct buffer){0};
}

static bool prepare_buffer(struct capture *capture)
{
    const struct constraints *constraints = &capture->constraints;
    if ((!constraints->argb && !constraints->xrgb) ||
        constraints->width > INT16_MAX || constraints->height > INT16_MAX)
        return false;

    uint32_t format = constraints->argb ? WL_SHM_FORMAT_ARGB8888 : WL_SHM_FORMAT_XRGB8888;
    struct buffer *buffer = &capture->buffer;
    int32_t width = constraints->width;
    int32_t height = constraints->height;
    if (buffer->proxy && buffer->width == width && buffer->height == height && buffer->format == format)
        return true;

    int32_t stride = width * 4;
    int64_t size = (int64_t)stride * height;
    if (size <= 0 || size > MAX_BUFFER_BYTES)
        return false;

    destroy_buffer(buffer);
    int fd = memfd_create("niri-edge-preview", MFD_CLOEXEC);
    if (fd < 0)
        return false;
    if (ftruncate(fd, size) < 0) {
        close(fd);
        return false;
    }

    uint32_t *pixels = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (pixels == MAP_FAILED) {
        close(fd);
        return false;
    }

    struct wl_shm_pool *pool = wl_shm_create_pool(capture->shm, fd, size);
    close(fd);
    struct wl_buffer *proxy = NULL;
    if (pool) {
        proxy = wl_shm_pool_create_buffer(pool, 0, width, height, stride, format);
        wl_shm_pool_destroy(pool);
    }
    if (!proxy) {
        munmap(pixels, size);
        return false;
    }

    *buffer = (struct buffer){
        .proxy = proxy,
        .pixels = pixels,
        .width = width,
        .height = height,
        .stride = stride,
        .size = size,
        .format = format,
    };
    return true;
}

static bool write_thumbnail(const struct capture *capture)
{
    const struct buffer *buffer = &capture->buffer;
    double scale = (double)capture->max_width / buffer->width;
    double vertical_scale = (double)capture->max_height / buffer->height;
    if (scale > vertical_scale)
        scale = vertical_scale;
    if (scale > 1)
        scale = 1;

    int32_t width = buffer->width * scale + 0.5;
    int32_t height = buffer->height * scale + 0.5;
    if (width == 0)
        width = 1;
    if (height == 0)
        height = 1;

    pixman_format_code_t format = buffer->format == WL_SHM_FORMAT_ARGB8888 ? PIXMAN_a8r8g8b8 : PIXMAN_x8r8g8b8;
    pixman_image_t *source = pixman_image_create_bits(format, buffer->width, buffer->height,
                                                     buffer->pixels, buffer->stride);
    if (!source)
        return false;

    pixman_image_t *thumbnail = pixman_image_create_bits(PIXMAN_a8r8g8b8, width, height, NULL, 0);
    if (!thumbnail) {
        pixman_image_unref(source);
        return false;
    }

    pixman_transform_t transform;
    pixman_transform_init_scale(&transform,
                               pixman_double_to_fixed((double)buffer->width / width),
                               pixman_double_to_fixed((double)buffer->height / height));
    pixman_image_set_transform(source, &transform);
    pixman_image_set_filter(source, PIXMAN_FILTER_BILINEAR, NULL, 0);
    pixman_image_set_repeat(source, PIXMAN_REPEAT_PAD);
    pixman_image_composite32(PIXMAN_OP_SRC, source, NULL, thumbnail, 0, 0, 0, 0, 0, 0, width, height);

    // The pipe carries two little-endian uint16 dimensions, then tightly packed premultiplied BGRA.
    uint8_t header[] = {width & 0xff, width >> 8, height & 0xff, height >> 8};
    size_t bytes = (size_t)width * height * 4;
    bool written = fwrite(header, sizeof(header), 1, stdout) == 1 &&
                   fwrite(pixman_image_get_data(thumbnail), bytes, 1, stdout) == 1 &&
                   fflush(stdout) == 0;

    pixman_image_unref(thumbnail);
    pixman_image_unref(source);
    return written;
}

static void frame_transform(void *data, struct ext_image_copy_capture_frame_v1 *frame, uint32_t transform)
{
    struct capture *capture = data;
    (void)frame;
    capture->transform = transform;
}

static void frame_damage(void *data, struct ext_image_copy_capture_frame_v1 *frame,
                         int32_t x, int32_t y, int32_t width, int32_t height)
{
    (void)data; (void)frame; (void)x; (void)y; (void)width; (void)height;
}

static void frame_time(void *data, struct ext_image_copy_capture_frame_v1 *frame,
                       uint32_t seconds_hi, uint32_t seconds_lo, uint32_t nanoseconds)
{
    (void)data; (void)frame; (void)seconds_hi; (void)seconds_lo; (void)nanoseconds;
}

static void frame_ready(void *data, struct ext_image_copy_capture_frame_v1 *frame)
{
    struct capture *capture = data;
    ext_image_copy_capture_frame_v1_destroy(frame);
    capture->frame = NULL;
    if (capture->stopped)
        return;

    capture->buffer.initialized = true;
    capture->retrying_buffer = false;

    // niri renders a toplevel in its own orientation, even on a rotated output.
    if (capture->transform != WL_OUTPUT_TRANSFORM_NORMAL || !write_thumbnail(capture))
        capture->stopped = true;

    capture->next_frame_ms = monotonic_ms() + FRAME_INTERVAL_MS;
}

static void frame_failed(void *data, struct ext_image_copy_capture_frame_v1 *frame, uint32_t reason)
{
    struct capture *capture = data;
    ext_image_copy_capture_frame_v1_destroy(frame);
    capture->frame = NULL;
    if (reason == EXT_IMAGE_COPY_CAPTURE_FRAME_V1_FAILURE_REASON_BUFFER_CONSTRAINTS && !capture->retrying_buffer) {
        // A resize can invalidate the buffer between submitting it and rendering the frame.
        destroy_buffer(&capture->buffer);
        capture->retrying_buffer = true;
        capture->next_frame_ms = monotonic_ms() + FRAME_INTERVAL_MS;
    } else {
        capture->stopped = true;
    }
}

static const struct ext_image_copy_capture_frame_v1_listener frame_listener = {
    .transform = frame_transform,
    .damage = frame_damage,
    .presentation_time = frame_time,
    .ready = frame_ready,
    .failed = frame_failed,
};

static bool request_frame(struct capture *capture)
{
    if (!prepare_buffer(capture))
        return false;

    struct ext_image_copy_capture_frame_v1 *frame = ext_image_copy_capture_session_v1_create_frame(capture->session);
    if (!frame)
        return false;

    capture->frame = frame;
    capture->transform = WL_OUTPUT_TRANSFORM_NORMAL;
    ext_image_copy_capture_frame_v1_add_listener(frame, &frame_listener, capture);
    ext_image_copy_capture_frame_v1_attach_buffer(frame, capture->buffer.proxy);
    // Reused buffers still contain the last frame. New buffers need a full copy.
    if (!capture->buffer.initialized)
        ext_image_copy_capture_frame_v1_damage_buffer(frame, 0, 0, capture->buffer.width, capture->buffer.height);
    ext_image_copy_capture_frame_v1_capture(frame);
    return true;
}

static void session_size(void *data, struct ext_image_copy_capture_session_v1 *session,
                         uint32_t width, uint32_t height)
{
    struct capture *capture = data;
    (void)session;
    capture->pending.width = width;
    capture->pending.height = height;
}

static void session_shm_format(void *data, struct ext_image_copy_capture_session_v1 *session, uint32_t format)
{
    struct capture *capture = data;
    (void)session;
    capture->pending.argb |= format == WL_SHM_FORMAT_ARGB8888;
    capture->pending.xrgb |= format == WL_SHM_FORMAT_XRGB8888;
}

static void session_dmabuf_device(void *data, struct ext_image_copy_capture_session_v1 *session,
                                  struct wl_array *device)
{
    (void)data; (void)session; (void)device;
}

static void session_dmabuf_format(void *data, struct ext_image_copy_capture_session_v1 *session,
                                  uint32_t format, struct wl_array *modifiers)
{
    (void)data; (void)session; (void)format; (void)modifiers;
}

static void session_done(void *data, struct ext_image_copy_capture_session_v1 *session)
{
    struct capture *capture = data;
    (void)session;
    capture->constraints = capture->pending;
    capture->pending = (struct constraints){0};
    if (!capture->constraints.width || !capture->constraints.height)
        capture->stopped = true;
}

static void session_stopped(void *data, struct ext_image_copy_capture_session_v1 *session)
{
    struct capture *capture = data;
    (void)session;
    capture->stopped = true;
}

static const struct ext_image_copy_capture_session_v1_listener session_listener = {
    .buffer_size = session_size,
    .shm_format = session_shm_format,
    .dmabuf_device = session_dmabuf_device,
    .dmabuf_format = session_dmabuf_format,
    .done = session_done,
    .stopped = session_stopped,
};

static void window_identifier(void *data, struct ext_foreign_toplevel_handle_v1 *window, const char *identifier)
{
    struct capture *capture = data;
    if (strcmp(identifier, capture->identifier) == 0)
        capture->window = window;
}

static void window_text(void *data, struct ext_foreign_toplevel_handle_v1 *window, const char *text)
{
    (void)data; (void)window; (void)text;
}

static void window_done(void *data, struct ext_foreign_toplevel_handle_v1 *window)
{
    struct capture *capture = data;
    if (capture->window != window)
        ext_foreign_toplevel_handle_v1_destroy(window);
}

static void window_closed(void *data, struct ext_foreign_toplevel_handle_v1 *window)
{
    struct capture *capture = data;
    if (capture->window == window) {
        capture->window = NULL;
        capture->stopped = true;
    }
    ext_foreign_toplevel_handle_v1_destroy(window);
}

static const struct ext_foreign_toplevel_handle_v1_listener window_listener = {
    .closed = window_closed,
    .done = window_done,
    .title = window_text,
    .app_id = window_text,
    .identifier = window_identifier,
};

static void list_window(void *data, struct ext_foreign_toplevel_list_v1 *list,
                        struct ext_foreign_toplevel_handle_v1 *window)
{
    (void)list;
    ext_foreign_toplevel_handle_v1_add_listener(window, &window_listener, data);
}

static void list_finished(void *data, struct ext_foreign_toplevel_list_v1 *list)
{
    struct capture *capture = data;
    ext_foreign_toplevel_list_v1_destroy(list);
    capture->list = NULL;
}

static const struct ext_foreign_toplevel_list_v1_listener list_listener = {
    .toplevel = list_window,
    .finished = list_finished,
};

static void registry_global(void *data, struct wl_registry *registry, uint32_t name,
                            const char *interface, uint32_t version)
{
    struct capture *capture = data;
    (void)version;
    if (strcmp(interface, wl_shm_interface.name) == 0) {
        capture->shm = wl_registry_bind(registry, name, &wl_shm_interface, 1);
    } else if (strcmp(interface, ext_foreign_toplevel_list_v1_interface.name) == 0) {
        capture->list = wl_registry_bind(registry, name, &ext_foreign_toplevel_list_v1_interface, 1);
        if (capture->list)
            ext_foreign_toplevel_list_v1_add_listener(capture->list, &list_listener, capture);
    } else if (strcmp(interface, ext_foreign_toplevel_image_capture_source_manager_v1_interface.name) == 0) {
        capture->sources = wl_registry_bind(registry, name, &ext_foreign_toplevel_image_capture_source_manager_v1_interface, 1);
    } else if (strcmp(interface, ext_image_copy_capture_manager_v1_interface.name) == 0) {
        capture->manager = wl_registry_bind(registry, name, &ext_image_copy_capture_manager_v1_interface, 1);
    }
}

static void registry_remove(void *data, struct wl_registry *registry, uint32_t name)
{
    (void)data; (void)registry; (void)name;
}

static const struct wl_registry_listener registry_listener = {
    .global = registry_global,
    .global_remove = registry_remove,
};

static int run_capture(struct capture *capture)
{
    struct wl_display *display = capture->display;
    struct wl_registry *registry = wl_display_get_registry(display);
    if (!registry)
        return 1;

    wl_registry_add_listener(registry, &registry_listener, capture);
    if (wl_display_roundtrip(display) < 0)
        return 1;
    if (!capture->shm || !capture->list || !capture->sources || !capture->manager)
        return UNAVAILABLE;
    if (wl_display_roundtrip(display) < 0 || !capture->window)
        return 1;

    struct ext_image_capture_source_v1 *source =
        ext_foreign_toplevel_image_capture_source_manager_v1_create_source(capture->sources, capture->window);
    if (!source)
        return 1;

    capture->session = ext_image_copy_capture_manager_v1_create_session(capture->manager, source, 0);
    ext_image_capture_source_v1_destroy(source);
    if (!capture->session)
        return 1;
    ext_image_copy_capture_session_v1_add_listener(capture->session, &session_listener, capture);

    while (!capture->stopped) {
        int timeout = -1;
        if (!capture->frame && capture->constraints.width) {
            int64_t remaining = capture->next_frame_ms - monotonic_ms();
            if (remaining > 0)
                timeout = remaining;
            else if (!request_frame(capture))
                return 1;
        }

        // A vanished reader must also end a capture that is waiting for window damage.
        struct pollfd fds[] = {
            {.fd = wl_display_get_fd(display), .events = POLLIN},
            {.fd = STDOUT_FILENO},
        };
        if (wl_display_flush(display) < 0) {
            if (errno != EAGAIN)
                return 1;
            fds[0].events |= POLLOUT;
        }

        if (poll(fds, 2, timeout) < 0) {
            if (errno == EINTR)
                continue;
            return 1;
        }
        if (fds[1].revents)
            return 0;
        if (fds[0].revents & POLLIN) {
            if (wl_display_dispatch(display) < 0)
                return 1;
        } else if (fds[0].revents & (POLLERR | POLLHUP | POLLNVAL)) {
            return 1;
        }
    }
    return 1;
}

static uint16_t parse_dimension(const char *value)
{
    char *end;
    unsigned long pixels = strtoul(value, &end, 10);
    return *end == '\0' && pixels <= UINT16_MAX ? pixels : 0;
}

int main(int argc, char **argv)
{
    if (argc != 4)
        return 1;

    struct capture capture = {
        .identifier = argv[1],
        .max_width = parse_dimension(argv[2]),
        .max_height = parse_dimension(argv[3]),
    };
    if (!capture.max_width || !capture.max_height)
        return 1;

    capture.display = wl_display_connect(NULL);
    if (!capture.display)
        return 1;

    int status = run_capture(&capture);
    destroy_buffer(&capture.buffer);
    wl_display_disconnect(capture.display);
    return status;
}
