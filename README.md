# RailScope Next

**简体中文** · [English](docs/README.en.md)

离线轨道交通地图与运行工作台。用来查看铁路和地铁网络、浏览场站资料、编排走行通道和车次时刻。

**[下载 Windows x64 安装包](https://github.com/yiqing1218/RailScope/releases/latest/download/RailScope-Next-windows-x64-setup.exe)** · [发行说明](https://github.com/yiqing1218/RailScope/releases/latest) · [提交问题](https://github.com/yiqing1218/RailScope/issues)

## 快速开始

1. 下载并运行 Windows x64 安装包。安装程序随附上海离线地图和 WebView2。
2. 从开始菜单打开 **RailScope Next**，在「使用指南」中点击「打开地图」。
3. 点击地图上的线路、站点或场站轮廓，也可以用左侧目录搜索。顶栏地球图标可切换中文和 English，问号可重新打开使用指南。

从源码使用时，先按[构建说明](railscope-next/README.md#启动)编译，再双击根目录的 `Start-RailScope.cmd`。已有 V1 运行计划可在「数据 → 导入旧版计划」中打开。

## 主要功能

| 工作台 | 可以做什么 |
| --- | --- |
| 地图 | 浏览铁路、地铁、公路与场站。场站点位和源轮廓联动，右侧显示属性、关联线路和来源。 |
| 运行 | 按端点和线路编排通道，编辑车次时刻，查看时间–距离图和上海地铁交路图。 |
| 分析 | 查询物理轨道连接、查看计划仿真和资源冲突。 |
| 数据 | 安装或切换离线快照，导入旧版运行计划。 |

## 离线数据

上海数据随安装包提供。全国及其他地区需另装兼容快照。在「数据 → 数据包 → 安装本地快照」填写包含 `manifest.json` 的目录路径。地图名称沿用快照中的源文字。

上海离线数据来自 OpenStreetMap，依据 ODbL 1.0 分发。发行页也提供独立数据 ZIP。运行和路径结果用于浏览与分析，几何关联以界面标示的证据为准。[数据署名与许可](docs/licenses/shanghai-offline-data.md)

## 更多信息

- [地图、目录与运行工作台使用说明](docs/workbench.md)
- [从源码构建和生成全国数据包](railscope-next/README.md)
- [铁路场站的点面关联和数据模型](docs/next-v7/railway-sites.md)
- [历史截图、场站图和运行数据参考](docs/reference/README.md)
