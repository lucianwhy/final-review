# Final Review Frontend

React + TypeScript + Vite 前端。默认打开“课程管理”工作台，可创建、编辑、归档和恢复课程，在一门课程下维护多场考试，并预览删除影响后确认删除。左上角“当前课程”可切换课程、打开该课程的普通聊天历史。“我的资料”可按课程上传、筛选和管理资料。“我的笔记”可找回草稿、编辑标题/考点 Markdown/来源引用、核对差异后确认，并查看正式与历史版本。其他入口包括 AI 对话、模拟测验和学习报告。

启动：

    cd D:\final_review\final-review\frontend
    npm install
    npm run dev

资料上传依赖后端资料 worker。在仓库根目录另开终端运行
`uv run python -m final_review.material_jobs`，或使用 `docker compose up --build -d`
同时启动 API 与 worker。上传后页面显示排队、处理阶段及失败原因，刷新会从服务端恢复状态。

终端会显示本地访问地址；通常为 http://127.0.0.1:5173。日常前端将 `/api` 转发到运行在 8080 端口的后端。

生产构建：

    npm run build

UI 组件：前端已安装 Ant Design（`antd`）。应用入口 `src/main.tsx` 通过 `ConfigProvider` 设置中文和项目主题色；可在 React 组件中按需引入，例如 `import { Button, Table } from "antd"`。课程页的“新建课程”按钮是现有用法示例。现有页面可以逐步替换组件，不需要克隆 Ant Design 源码仓库。

前端端到端测试（需要本机 Chrome，命令会启动临时内存后端和 Vite）：

    npm run test:e2e
