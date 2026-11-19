# niri Edge Switcher

一个接近 PaperWM 边缘切换手感的 `niri + Python` 小工具。

当前实现能力：

- 左右屏幕边缘各有一条 1px 热区
- 仅在该侧存在可切换的边缘窗口时，对应热区才会显示；否则该侧边缘会直接穿透
- 鼠标悬停边缘一小段时间后，显示该侧目标窗口的实时缩略图和标题。缩略图等比缩放，最大 320 × 200 逻辑像素，按显示器缩放匹配设备像素，最高每秒更新 10 帧
- 点击边缘热区可直接聚焦目标窗口
- 点击预览卡片也可聚焦目标窗口
- 跟随 `niri` IPC event stream 更新窗口、工作区和输出状态
- 多输出环境下会为每个输出各自创建左右热区

当前限制：

- 热区必须拦截最边缘那 1px 指针事件，不能完全穿透
- 当 `tile_pos_in_workspace_view` 缺失时，会退回到列位置和最近使用时间推断左右目标

## 运行

直接运行已打包的 flake app：

```bash
nix run github:iahccc/niri-edge-switcher
```

本地开发：

```bash
nix develop path:.#
python main.py
```

默认需要在 `niri` Wayland 会话内运行，并且环境中已经有 `NIRI_SOCKET`。

## 实时预览

实时预览需要当前 niri 会话提供窗口列表、单窗口图像源和 `ext-image-copy-capture-v1`。程序收到首帧后才显示预览卡片，最多等待 1.5 秒。接口缺失或取帧失败时，仍可点击边缘热区切换窗口。

移开鼠标并收起卡片后，程序释放捕获资源。画面遵循 niri 的 `block-out-from "screen-capture"` 窗口规则。

## 可调参数

```bash
python main.py --help
```

比较有用的参数：

- `--edge-width`: 热区宽度，默认 `1`
- `--preview-delay-ms` 控制悬停多久后显示预览卡片
- `--post-click-delay-ms` 控制点击边缘切换后，鼠标仍停在边缘时多久显示下一张卡片
- `--preview-margin` 控制卡片离屏幕边缘的距离
- `--inter-column-spacing`: 当 `niri` 没有提供 `tile_pos_in_workspace_view` 时，手动覆盖列间距估算

## 测试

选择算法的纯逻辑单测不依赖 GTK：

```bash
python -m unittest discover -s tests
```

在 `nix develop path:.#` 环境中运行同一条命令，也会验证高分屏取帧、取消和首帧超时。
