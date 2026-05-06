---
name: internet-trending
description: 获取中国互联网各平台实时热搜榜单，包括微博、知乎、百度、B站、今日头条、抖音等，以及 GitHub、V2EX 等技术类热榜。当用户询问"现在有什么热点"、"最近有哪些热搜"、"今天的热门话题"、"网络热点"、"热榜"、"社会热点"时使用。
---

# 获取网络热搜热榜

## 工具使用策略

优先使用 **WebFetch** 直接抓取网页；若返回空内容或被重定向至登录页，改用 **WebSearch** 搜索平台热搜关键词作为兜底。

---

## 聚合榜单（首选）

聚合多平台热榜，一次获取覆盖面广：

| 平台 | URL | 包含来源 |
|------|-----|---------|
| 热榜今日 | https://rebang.today | 微博、抖音、B站、知乎、头条等 |

---

## 各平台官方榜单

### 社交 / 资讯

| 平台 | URL | 备注 |
|------|-----|------|
| 微博热搜 | https://s.weibo.com/top/summary | 偶尔需要登录 Cookie，可改用聚合站 |
| 知乎热榜 | https://www.zhihu.com/hot | 偶尔需要登录，聚合站可兜底 |
| 百度热搜 | https://top.baidu.com/board | 稳定可访问 |
| 今日头条热榜 | https://www.toutiao.com/hot-event/hot-board/ | 稳定可访问 |
| 抖音热搜 | https://www.douyin.com/discover | 以 App 为主，网页版可能受限 |

### 视频 / 娱乐

| 平台 | URL | 备注 |
|------|-----|------|
| B站热门 | https://www.bilibili.com/v/popular/rank/all | 稳定可访问 |
| 豆瓣热门 | https://movie.douban.com/chart | 电影热榜 |

### 技术 / 产品

| 平台 | URL | 备注 |
|------|-----|------|
| GitHub Trending | https://github.com/trending | 全球开发者热门项目 |
| V2EX 热门 | https://www.v2ex.com/?tab=hot | 中文技术社区 |
| 少数派 | https://sspai.com | 科技产品话题 |

---

## 工作流程

1. **明确用户需求**：是要全平台热点、特定平台热搜，还是某一领域（科技/娱乐/社会）的热点？
2. **选择入口**：
   - 宽泛热点 → 首选 `tophub.today` 或 `rebang.today`
   - 指定平台 → 使用对应官方 URL
   - 技术方向 → 优先 `github.com/trending` + `v2ex.com`
3. **抓取内容**：用 WebFetch 获取页面，提取热搜词条和热度数值
4. **呈现结果**：按排名列出，附简要说明；若信息来自多平台，按平台分组展示

---

## WebSearch 兜底模板

当 WebFetch 失败（需登录 / 内容为空）时，用以下格式搜索：

```
微博热搜 今日 site:weibo.com
知乎热榜 今天
百度热搜榜 今日热点
```
