import { z } from "zod";

export const AccountResponse = z.object({
  tier: z.string(),
  status: z.string().optional(),
  current_period_end: z.number().optional(),
  subscription_id: z.string().optional(),
});

export type AccountResponse = z.infer<typeof AccountResponse>;

export const HealthResponse = z.object({
  ok: z.boolean(),
  events_count: z.number().optional(),
  version: z.string().optional(),
});

export type HealthResponse = z.infer<typeof HealthResponse>;

export const BaselineItem = z.object({
  match_level: z.string().optional(),
  action: z.string().optional(),
  cost_tokens: z.number().optional(),
  p50: z.number().optional(),
  p25: z.number().optional(),
  p75: z.number().optional(),
  sample_count: z.number().optional(),
});

export const BaselinesResponse = z.union([z.array(BaselineItem), z.object({ baselines: z.array(BaselineItem) })]);

export type BaselinesResponse = z.infer<typeof BaselinesResponse>;

export const DeviceAuthResponse = z.object({
  user_code: z.string(),
  verification_uri: z.string(),
  interval: z.number(),
  device_code: z.string(),
  expires_in: z.number().optional().default(900),
});

export type DeviceAuthResponse = z.infer<typeof DeviceAuthResponse>;

export const PollAuthResponse = z.object({
  access_token: z.string().optional(),
  error: z.string().optional(),
  error_description: z.string().optional(),
});

export type PollAuthResponse = z.infer<typeof PollAuthResponse>;

export const ApiKeyResponse = z.object({
  api_key: z.string(),
});

export type ApiKeyResponse = z.infer<typeof ApiKeyResponse>;

export const SubscribeResponse = z.object({
  checkout_url: z.string(),
});

export type SubscribeResponse = z.infer<typeof SubscribeResponse>;

export const PortalResponse = z.object({
  url: z.string(),
});

export type PortalResponse = z.infer<typeof PortalResponse>;

export const ExportCalibration = z.object({
  action: z.string().optional(),
  expected_cost: z.number().nullable().optional(),
  actual_cost: z.number().nullable().optional(),
  outcome: z.string().optional(),
  session_id: z.string().optional(),
  created_at: z.union([z.number(), z.string()]).optional(),
});

export const ExportSummary = z.object({
  total_calibrations: z.number().optional(),
  accuracy_by_action: z
    .array(
      z.object({
        action: z.string().optional(),
        sample_count: z.number().optional(),
        mean_deviation: z.number().optional(),
        mape: z.number().optional(),
      }),
    )
    .optional(),
});

export const ExportResponse = z.object({
  exported_at: z.number(),
  tier: z.string().optional(),
  calibrations: z.array(ExportCalibration).optional(),
  summary: ExportSummary.optional(),
});

export type ExportResponse = z.infer<typeof ExportResponse>;

export const ErrorDetailResponse = z.object({
  detail: z.string().optional(),
});

export type ErrorDetailResponse = z.infer<typeof ErrorDetailResponse>;
