# RPM 图标

`jflove-256.png` 由 `jflove-desktop/images/icon.png`（2048×2048）等比缩放而来，
供 RPM 安装到 `/usr/share/icons/hicolor/256x256/apps/jflove.png`。

重新生成（任选其一）：

```bash
# 1) 用桌面端 venv 的 PySide6（无需额外依赖）
jflove-desktop/venv-win/Scripts/python.exe -c "from PySide6.QtGui import QImage; from PySide6.QtCore import Qt; i=QImage('jflove-desktop/images/icon.png').scaled(256,256,Qt.KeepAspectRatio,Qt.SmoothTransformation); i.save('jflove-desktop/packaging/linux/icons/jflove-256.png','PNG')"

# 2) 有 ImageMagick / librsvg 时
magick jflove-desktop/images/icon.png -resize 256x256 jflove-desktop/packaging/linux/icons/jflove-256.png
```

> 注意：源图 `images/icon.png` 是不带 alpha 通道的 RGB 图（圆角图标 + 浅色底），
> 因此本图标是整块方图而非镂空图；若要镂空版，请换成带透明通道的源图再重新生成。
