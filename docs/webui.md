# TaskHub WebUI

监控面板 / 全部任务 / 统计 / API 文档四页合一的 React SPA，挂在服务的 `/ui` 路径下。

## 技术栈

与 nsfw-check webui 同栈：React 19 + Vite 8 (Rolldown) + TypeScript 7（原生编译器）+ Motion 14 (`motion/react`) + Tailwind CSS v4（CSS-first `@theme inline`）+ lucide-react 1.x。
图表为零图表库手写实现：SVG 折线图（多序列 + 渐变面积 + hover tooltip）与 SVG 环形图，深浅主题自动跟随语义色变量。

## 目录

- `webui/` — SPA 源码（Vite 项目）
- `webui/dist/` — 构建产物（不入库，FastAPI 由 `server.frontend_dir` 挂载直出）
- 旧版 `frontend/*.html` 静态页已删除（可从 git 历史找回）

## 开发

```bash
# 后端（终端 1）
uv run python main.py start          # :9000

# 前端 dev server（终端 2）
cd webui && npm install && npm run dev   # :5174，访问 http://localhost:5174/ui/
#                                        （vite base=/ui/，API/WS 由 Vite 代理到 9000）
```

## 构建与生效

```bash
cd webui && npm run build   # tsc --noEmit 类型检查 + vite build → dist/
```

- 改前端：只需重新 `npm run build` + 浏览器刷新（`/ui` 有 no-cache 中间件）。
- 改后端：必须重启 9000 进程。
- dist 不存在时 `/ui` 自动禁用（仅告警），后端 API 不受影响。
- 首次部署/换机器：先 `npm install` 再 `npm run build`。

## 本地存储键

- `taskhub:theme` — 主题三态（light/dark/system）
- `taskhub:default-show` — 监控面板任务表默认展示条数
