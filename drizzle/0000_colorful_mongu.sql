CREATE TABLE `calibration_events` (
	`id` text PRIMARY KEY NOT NULL,
	`action` text NOT NULL,
	`phase_label_tokens` text,
	`expected_cost` real NOT NULL,
	`actual_cost` real NOT NULL,
	`outcome` text NOT NULL,
	`identity_id` text NOT NULL,
	`project_type` text DEFAULT 'software' NOT NULL,
	`route_id` text,
	`phase_id` text,
	`synced` integer DEFAULT 0 NOT NULL,
	`created_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `completed_sequences` (
	`id` text PRIMARY KEY NOT NULL,
	`action_sequence` text NOT NULL,
	`total_expected` real NOT NULL,
	`total_actual` real NOT NULL,
	`efficiency` real NOT NULL,
	`created_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `correction_events` (
	`id` text PRIMARY KEY NOT NULL,
	`calibration_event_id` text NOT NULL,
	`previous_actual` real NOT NULL,
	`corrected_actual` real NOT NULL,
	`created_at` text NOT NULL,
	FOREIGN KEY (`calibration_event_id`) REFERENCES `calibration_events`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE TABLE `cost_baselines` (
	`id` text PRIMARY KEY NOT NULL,
	`match_level` text NOT NULL,
	`action` text NOT NULL,
	`avg_cost` real NOT NULL,
	`ci_lo` real,
	`ci_hi` real,
	`sample_count` integer NOT NULL,
	`created_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `route_phases` (
	`id` text PRIMARY KEY NOT NULL,
	`route_id` text NOT NULL,
	`label` text NOT NULL,
	`label_tokens` text NOT NULL,
	`action` text NOT NULL,
	`expected_cost` real,
	`actual_cost` real,
	`status` text DEFAULT 'pending' NOT NULL,
	`sequence` integer NOT NULL,
	`hazards` text,
	`deviation` real,
	`created_at` text NOT NULL,
	FOREIGN KEY (`route_id`) REFERENCES `routes`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE TABLE `routes` (
	`id` text PRIMARY KEY NOT NULL,
	`project` text NOT NULL,
	`goal` text NOT NULL,
	`goal_tokens` text NOT NULL,
	`status` text DEFAULT 'active' NOT NULL,
	`identity_id` text NOT NULL,
	`project_type` text DEFAULT 'software' NOT NULL,
	`total_expected` real,
	`total_actual` real,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `schema_version` (
	`version` integer NOT NULL
);
