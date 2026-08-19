import { defineConfig, loadEnv, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";
import { BRAND_DEFAULTS, brandDocumentTitle } from "./src/brand-defaults.ts";

function escapeHtml(value: string): string {
    return value
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

/**
 * 把 index.html 的 <title> 与 <meta name="description"> 换成品牌值。
 *
 * main.tsx 启动时也会覆盖这两处，但那要等 JS 执行完；在此之前标签页会短暂显示
 * HTML 里的字面量。构建期就替换掉，白牌部署首屏不会闪出上游品牌名，同时也让
 * 不执行 JS 的抓取方（分享卡片、爬虫）读到正确的名称与描述。
 *
 * 默认值与运行时共用 src/brand-defaults.ts，两处不会漂移。
 */
function brandIndexHtml(env: Record<string, string>): Plugin {
    const pick = (value: string | undefined, fallback: string): string => {
        const trimmed = (value ?? "").trim();
        return trimmed.length > 0 ? trimmed : fallback;
    };
    const name = pick(env.VITE_BRAND_NAME, BRAND_DEFAULTS.name);
    const tagline = pick(env.VITE_BRAND_TAGLINE, BRAND_DEFAULTS.tagline);
    const description = pick(env.VITE_BRAND_DESCRIPTION, BRAND_DEFAULTS.description);

    return {
        name: "brand-index-html",
        transformIndexHtml(html) {
            return html
                .replace(
                    /<title>[\s\S]*?<\/title>/,
                    `<title>${escapeHtml(brandDocumentTitle(name, tagline))}</title>`,
                )
                .replace(
                    /(<meta\s+name="description"\s+content=")[\s\S]*?(")/,
                    `$1${escapeHtml(description)}$2`,
                );
        },
    };
}

export default defineConfig(({ mode }) => {
    // 第三参数 "" = 不限前缀地读取 .env 文件；这里只取 VITE_BRAND_* 使用。
    const env = loadEnv(mode, __dirname, "");

    return {
        plugins: [react(), tailwindcss(), brandIndexHtml(env)],
        resolve: {
            alias: { "@": path.resolve(__dirname, "src") },
            extensions: [".mjs", ".mts", ".ts", ".tsx", ".js", ".jsx", ".json"],
        },
        server: {
            host: "0.0.0.0",
            port: 5173,
            proxy: {
                "/api": {
                    target: "http://127.0.0.1:1241",
                    changeOrigin: true,
                },
            },
        },
        build: {
            outDir: "dist",
            emptyOutDir: true,
        },
    };
});
