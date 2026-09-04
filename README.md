# jellyfin-nfo-generator

通过 [Bangumi](https://bgm.tv) API 为本地动漫视频文件生成 Jellyfin 兼容的 NFO 元数据文件，配合 [jellyfin-plugin-bangumi](https://github.com/kookxiang/jellyfin-plugin-bangumi) 使用。

**纯 Python 标准库实现，无需安装第三方依赖。**

## 为什么需要这个？

- 番剧文件夹命名为 `番剧名 (YYYYQX)` 时，Jellyfin 插件无法正确识别番剧名
- 不同压制组/字幕组的文件命名规则各异，AnitomySharp 难以精确匹配集数
- 不想为了自动刮削而重命名视频文件

本脚本通过在媒体库扫描前预写 NFO 文件中的 `<bangumiid>`，配合插件的「始终根据配置的 Bangumi ID 获取元数据」功能，确保每集都能正确匹配。

## 环境要求

- Python 3.10+
- 无需安装任何第三方包

## 快速开始

1. 在 [Bangumi 开发者平台](https://bgm.tv/dev/app) 创建应用，获取 `APP_ID` 和 `APP_SECRET`
2. 填写到脚本顶部的对应变量中
3. 运行脚本：

```bash
python jellyfin-nfo-generator.py
```

4. 首次运行会自动打开浏览器进行 OAuth 授权
5. 输入番剧目录的绝对路径，例如：

```
请输入番剧目录路径：D:\Anime\别当欧尼酱了 (2023Q4)
```

## 功能特性

### NFO 生成

- 根据文件夹名自动搜索 Bangumi 番剧
- 生成 `tvshow.nfo`（番剧信息）和各集 `.nfo`（剧集信息）
- 自动处理季度首话不为 ep.01 的偏移情况
- 单集剧场版等特殊情况自动识别
- 支持多种文件名格式的集数提取（`[01]`、`第01话`、`#01` 等）

### 字幕规范化

检测目录下的字幕文件，自动提示将非标准命名转换为 Jellyfin 语言代码：

| 原后缀 | → | 目标 | 说明 |
|---|---|---|---|
| `.sc` | → | `.zh` | 简体中文 |
| `.chs` | → | `.zh` | 简体中文 |
| `.scjp` | → | `.zh` | 简中+日文 |
| `.tc` | → | `.zh-Hant` | 繁体中文 |
| `.cht` | → | `.zh-Hant` | 繁体中文 |

## 文件命名建议

建议番剧文件夹命名为 `番剧名 (YYYYQX)` 格式，例如：

```
别当欧尼酱了 (2023Q4)
├── tvshow.nfo
├── [SubGroup] Don't Become an Otaku... [01] [1080p].mkv
├── [SubGroup] Don't Become an Otaku... [01] [1080p].nfo
├── [SubGroup] Don't Become an Otaku... [02] [1080p].mkv
├── [SubGroup] Don't Become an Otaku... [02] [1080p].nfo
└── ...
```

## 注意事项

- 用户认证信息保存在脚本运行目录下的 `bangumi.json`，下次启动时自动刷新
- 如果 Jellyfin 容器以更高权限运行并已生成 NFO，脚本会因权限不足跳过对应文件
- 集数匹配失败时会列出未能匹配的集数，可手动检查文件名格式

## 相关项目

- [jellyfin-plugin-bangumi](https://github.com/kookxiang/jellyfin-plugin-bangumi) — Jellyfin Bangumi 元数据插件
- [Bangumi API](https://github.com/bangumi/api) — Bangumi API 文档
