# 第三方组件

本程序的打包版包含以下开源运行时与依赖；随发行包提供的许可文件已收集至 `licenses` 目录，部分也保留在 `_internal` 的包或发行元数据目录中。

- [Python](https://www.python.org/psf/license/)：PSF License。
- [Tcl/Tk](https://www.tcl.tk/software/tcltk/license.html)：Tcl/Tk 许可。
- [RapidOCR](https://github.com/RapidAI/RapidOCR)：Apache-2.0；其发布包包含基于 PaddleOCR 的 OCR 模型。
- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)：Apache-2.0。
- [ONNX Runtime](https://github.com/microsoft/onnxruntime)：MIT。
- [Pillow](https://github.com/python-pillow/Pillow)：MIT-CMU。
- [OpenCV](https://github.com/opencv/opencv)：Apache-2.0（随包附带其他组件许可）。
- [NumPy](https://github.com/numpy/numpy)：BSD-3-Clause，随包附带相关运行库许可。
- [Shapely](https://github.com/shapely/shapely)：BSD-3-Clause，包含 GEOS 相关许可。
- OmegaConf、ANTLR Python runtime、PyYAML、requests 及其他间接依赖：以各发行包随附的许可文件为准。

本项目与 EA、Respawn、Apex Legends 没有官方合作或授权关系；游戏名称仅用于说明用途。用户提供的样例截图只作为本地开发验证材料，便携发布包不包含此截图或开发测试数据。

## 许可范围与识别数据

项目原创Python代码和文档采用MIT（见LICENSE）。EA／Respawn的名称、游戏界面、美术和商标不属于本项目原创资产，本项目不授予它们MIT许可。`tracker/summary_templates.json`是仅用于识别界面文字形状的功能模板数据，不是本项目拥有的游戏美术许可；不能据此取得游戏素材再分发权。公开源包不包含完整游戏截图或网页图片，MIT也不覆盖任何外部素材。

模型许可以RapidOCR／PaddleOCR发布包为准，不因打包改变。Windows中文字体由操作系统提供，程序不分发微软字体文件。便携包licenses包含当前依赖随包许可；构建者升级依赖时应重新收集。项目没有使用官方Apex徽标、角色图或用户截图作为公共装饰。
