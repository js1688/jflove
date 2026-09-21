/**
 * 桌面端专属常量
 *
 * 与 Web 端 `jflove-web/src/config/constants.ts` 分开的原因：Web 端那几个常量
 * 与"浏览器同源"语义绑定（例如 `DEFAULT_SERVER_URL = ''` 表示同源，
 * 请求走相对路径由 nginx 反代），而**桌面端不存在"同源"这回事** ——
 * 桌面端必须显式指定完整的服务端地址，否则无从连接。
 */

/** 桌面端默认服务端地址（首次启动、地址历史为空时使用） */
export const DEFAULT_DESKTOP_SERVER_URL = 'http://127.0.0.1:8989';
