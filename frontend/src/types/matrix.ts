/** Matrix 托管态下的设置页数据。 */

export interface MatrixModelInfo {
  model_id: string;
  display_name: string;
  media_type: "text" | "image" | "video" | "audio";
}

export interface MatrixOverview {
  /** false = 独立部署，设置页走原来的供应商配置界面 */
  enabled: boolean;
  /** 是否已完成握手并拿到网关凭据 */
  connected?: boolean;
  /** 只有主机名，不含协议与路径；api key 永不下发 */
  gateway_host?: string | null;
  media_counts?: Record<string, number>;
  models?: MatrixModelInfo[];
  /** Matrix 控制台地址，用于"管理账户/充值"这类跳转 */
  matrix_web_url?: string;
}
