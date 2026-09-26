# 开发说明

当前桌面入口是 `desktop/launcher.py`，使用 PySide6 / Qt WebEngine。
`desktop/src-tauri` 是保留的 Web 打包壳，不是 `Start-RailScope.cmd` 使用的运行入口。

## 本地验证

在仓库根目录执行：

```powershell
python -m pip install -e "./backend[dev]" -r desktop/requirements.txt pytest-qt
$env:PYTHONPATH = "$PWD\desktop;$PWD\backend;$PWD"
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest backend/tests desktop/tests -q --basetemp .pytest-work/full
node --check desktop/assets/map.js
```

Web 界面使用 Node.js 22.12+ 的兼容版本，在 `frontend/` 中运行 `npm ci`、
`npm test -- --run` 和 `npm run build`。Qt 离屏测试不代替显卡渲染、在线底图和长时间交互测试。

## 数据边界

- `backend/railscope/domain.py` 为共享领域契约；桌面 SQLite 与后端服务适配该契约。
- 原始 PBF、GeoJSON 和生成 SQLite 索引放在 `data/raw/`、`data/processed/`，不提交 Git。
- 工作区人工编辑保存在 `data/user_settings/`，不得回写原始 OSM 文件。
- 地铁线路、地铁站和高速公路目录采用 SQLite 分页模型。国铁目录的完整 Model/View 迁移仍未完成，不能将去掉逐行 QWidget 等同于所有目录已经虚拟化。
- 地图按当前 bbox 和目录选择读取基础设施，不在 `/config.json` 中发送全国地铁站点或站区几何。

## 可选 PostGIS 服务

根目录执行 `docker compose up -d db`，在 `backend/` 使用 `python -m alembic upgrade head`
应用迁移。示例 API 和核心领域测试使用独立的示例仓库，不要求外部数据库运行。

提交前运行与修改范围匹配的验证，检查差异与生成文件，按 `AGENTS.md` 要求独立提交。
完整启动、操作和目录结构见 [README](../README.md)。
