# niri Edge Switcher

一个接近 PaperWM 边缘切换手感的 `niri + Python` 小工具。

当前实现能力：

- 左右屏幕边缘各有一条 1px 热区
- 仅在该侧存在可切换的边缘窗口时，对应热区才会显示；否则该侧边缘会直接穿透
- 鼠标悬停边缘一小段时间后，显示该侧最靠近屏幕边缘的窗口应用图标
- 点击边缘热区可直接聚焦目标窗口
- 点击图标卡片也可聚焦目标窗口
- 跟随 `niri` IPC event stream 更新窗口、工作区和输出状态
- 多输出环境下会为每个输出各自创建左右热区

当前限制：

- 热区必须拦截最边缘那 1px 指针事件，不能完全穿透
- 图标优先按 `app_id` 和桌面文件解析；如果应用没有可用图标，会回退到通用应用图标
- 当 `tile_pos_in_workspace_view` 缺失时，会退回到列位置和最近使用时间推断左右目标

## 运行

```bash
nix develop
python main.py
```

默认需要在 `niri` Wayland 会话内运行，并且环境中已经有 `NIRI_SOCKET`。

## 可调参数

```bash
python main.py --help
```

比较有用的参数：

- `--edge-width`: 热区宽度，默认 `1`
- `--preview-delay-ms`: hover 多久后显示图标卡片
- `--post-click-delay-ms`: 点击边缘切换后，如果鼠标仍停在边缘，多快显示下一张图标卡片
- `--icon-size`: 图标大小，默认 `72`
- `--preview-margin`: 图标卡片离屏幕边缘的距离
- `--inter-column-spacing`: 当 `niri` 没有提供 `tile_pos_in_workspace_view` 时，手动覆盖列间距估算

## 测试

选择算法的纯逻辑单测不依赖 GTK：

```bash
python -m unittest discover -s tests
```
