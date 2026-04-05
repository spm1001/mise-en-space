/**
 * User Configuration
 *
 * Edit these settings for your setup. This file is NOT auto-generated
 * and won't be overwritten by deployments.
 */

// ============================================================================
// FOLDER SETTINGS
// ============================================================================

/**
 * Name of the root folder in your Drive where attachments are stored.
 * Will be created automatically if it doesn't exist.
 *
 * WARNING: mise-en-space auto-discovers this folder by name. If you rename
 * it, set MISE_EMAIL_ATTACHMENTS_FOLDER_ID env var to the folder's Drive ID.
 */
const ROOT_FOLDER_NAME = 'Email Attachments';

// ============================================================================
// EMAIL EXCLUSIONS
// ============================================================================

/**
 * Subject patterns to exclude (case-insensitive, partial match).
 * Emails with subjects containing these strings will be skipped entirely.
 *
 * These are applied in the Gmail search query for efficiency.
 *
 * Common patterns to consider adding:
 * - Newsletter names you receive
 * - Automated report subjects
 * - Calendar invitation responses
 */
const EXCLUDED_SUBJECT_PATTERNS = [
  // Calendar responses (English)
  'Invitation',
  'Accepted',
  'Declined',
  'Cancelled',
  'Canceled',
  'Updated invitation',
  'Proposed new time',
  // Calendar responses (German)
  'Angenommen',
  'Abgelehnt',
  'Einladung',
  // Calendar responses (French)
  'Accepté',
  'Refusé',
  // Common automated emails
  'Delivery Status Notification',
  'Out of Office',
  'Automatic reply',
  // Add your newsletters/reports below:
  // 'Weekly Digest',
  // 'Daily Summary',
];

/**
 * TO: addresses to skip (emails sent to these addresses are ignored).
 * Useful for skipping mailing lists you're on but don't want attachments from.
 */
const EXCLUDED_TO_ADDRESSES = [
  // Examples:
  // 'all-company@yourcompany.com',
  // 'team-announcements@yourcompany.com',
];

/**
 * FROM: addresses to skip (emails from these senders are ignored).
 * Useful for skipping automated systems or notification services.
 */
const EXCLUDED_FROM_ADDRESSES = [
  // Examples:
  // 'noreply@service.com',
  // 'notifications@app.com',
];

// ============================================================================
// PROCESSING SETTINGS
// ============================================================================

/**
 * Maximum messages to process per trigger invocation.
 * Lower = safer for execution time limits.
 * Higher = faster backfill.
 *
 * Recommended: 50 for 15-minute triggers, 100 for hourly.
 */
const CHUNK_SIZE = 50;

/**
 * Years to include in backfill processing.
 * The chunk functions will process these years.
 * Add new years as needed (e.g., 2026 in January 2026).
 */
const BACKFILL_YEARS = [2023, 2024, 2025, 2026];
