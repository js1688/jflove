/**
 * 内容层：官网所有数据的唯一入口。
 *
 * 设计约束（对应 AGENTS.md §10.2 与门禁⑥）：
 *   **页面禁止硬编码**特性 / 接口 / 版本 / 明文白名单 —— 一律从这里取。
 *   这里的 JSON 由 `website/scripts/extract_sources.py` 与 `gen_api_docs.py`
 *   从项目真身现场生成，改代码不重跑 ⇒ 门禁①/⑩ 会红。
 */

import featuresJson from '../../content/features.json';
import routesJson from '../../content/api-routes.json';
import metaJson from '../../content/meta.json';
import openapiJson from '../../content/api-openapi.json';

export interface FeatureSupport {
  [platform: string]: string;
}

export interface Feature {
  name: string;
  summary: string;
  supports: FeatureSupport;
}

export interface FeaturesManifest {
  source: string;
  platforms: string[];
  count: number;
  features: Feature[];
}

export interface ApiRoute {
  method: string;
  path: string;
  summary: string;
}

export interface ApiController {
  file: string;
  prefix: string;
  tags: string[];
  count: number;
  routes: ApiRoute[];
}

export interface ApiRoutesManifest {
  source: string;
  count: number;
  controllers: ApiController[];
}

export interface MetaManifest {
  product: string;
  version: string;
  plain_paths: string[];
  sources: string[];
}

export interface OpenApiOperation {
  method: string;
  path: string;
  summary: string;
  description: string;
  tag: string;
  operationId: string;
  plain: boolean;
  hasRequestBody: boolean;
}

export interface OpenApiGroup {
  tag: string;
  count: number;
  operations: OpenApiOperation[];
}

export interface OpenApiManifest {
  source: string;
  openapi: string;
  info: { title: string; version: string };
  total: number;
  groupCount: number;
  groups: OpenApiGroup[];
  plainPaths: string[];
}

export const features = featuresJson as FeaturesManifest;
export const apiRoutes = routesJson as ApiRoutesManifest;
export const meta = metaJson as MetaManifest;
export const openapi = openapiJson as OpenApiManifest;

/** 当前官网版本 = 项目版本（由 sync_version.py 同步，禁止手改） */
export const siteVersion: string = meta.version;

/** 三端展示名 → 用于特性矩阵表头 */
export const platforms: string[] = features.platforms;

/**
 * 平台支持判定。
 * 值只有两种：`✅`（支持）与 `—`（不支持），来自 README 速览表。
 * 用函数而不是散落各处的字符串比较，避免把 `—` 写成 `-` 之类。
 */
export function supports(feature: Feature, platform: string): boolean {
  return feature.supports[platform] === '✅';
}

/** 统计某平台支持的特性数（首页 / 功能页用于展示「N/11」） */
export function supportCount(platform: string): number {
  return features.features.filter((f) => supports(f, platform)).length;
}

/** 全部接口扁平化（API 页检索用） */
export const allOperations: OpenApiOperation[] = openapi.groups.flatMap(
  (g) => g.operations,
);
