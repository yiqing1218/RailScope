# 开发说明

当前桌面入口是 `desktop/launcher.py`，使用 PySide6 / Qt WebEngine。
`desktop/src-tauri` 是保留的 Web 打包壳，不是 `Start-RailScope.cmd` 使用的运行入口。

## 本地验证

在仓库根目录执行：

```powershell
python -m pip install -e "./backend[dev]" -r desktop/requirements.txt pytest-qt
python -m pytest backend/tests desktop/tests -q -p no:cacheprovider --basetemp .audit-local
node --check desktop/assets/map.js
node desktop/tests/road_layers.cjs
node desktop/tests/vehicle_motion_test.cjs
```

Web 界面使用 Node.js 22.12+ 的兼容版本，在 `frontend/` 中运行 `npm ci`、
`npm test -- --run` 和 `npm run build`。Qt 离屏测试不代替显卡渲染、在线底图和长时间交互测试。

## 数据边界

- `backend/railscope/domain.py` 为共享领域契约；桌面 SQLite 与后端服务适配该契约。
- 原始 PBF、GeoJSON 和生成 SQLite 索引放在 `data/raw/`、`data/processed/`，不提交 Git。
- 国铁目录工作区保存在 `data/user_settings/rail_catalog.json`；目录交换文件是种子或显式导入导出，不会随每次编辑自动改写。计划与身份库的历史默认路径仍在 `data/processed/operations/`，迁移前必须保护这些用户文件，详见系统审计报告。
- 国铁、地铁和高速公路的主要目录使用 SQLite 分页 Model/View，每页 128 项。国铁仍保留最多 512 项的旧编辑辅助树；站点投影刷新和部分编辑对话框还存在整表/整树工作，不代表所有界面已经增量更新。
- `railscope.workspace` 保存项目实体、场景和命令；占用、冲突是派生结果，载入和撤销时重新计算，不作为项目数据持久化。
- 地图按当前 bbox 和目录选择读取基础设施，不在 `/config.json` 中发送全国地铁站点或站区几何。

## 可选 PostGIS 服务

根目录执行 `docker compose up -d db`，在 `backend/` 使用 `python -m alembic upgrade head`
应用迁移。示例 API 和核心领域测试使用独立的示例仓库，不要求外部数据库运行。

提交前运行与修改范围匹配的验证，检查差异与生成文件，按 `AGENTS.md` 要求独立提交。
完整启动、操作和目录结构见 [README](../README.md)。
启动和数据流、已修正问题、剩余架构债务与验证范围见 [系统审计](audit/SYSTEM_AUDIT.md)。
