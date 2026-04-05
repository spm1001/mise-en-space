/**
 * Filter configuration — shared with the Python MCP server.
 * Source of truth: config/attachment_filters.json in the repo root.
 *
 * If you have itv-appscript: regenerate with `itv-appscript deploy`
 * Otherwise: edit this file directly to match your needs.
 */

const FILTER_CONFIG_VERSION = 1;
const IMAGE_SIZE_THRESHOLD = 204800;

const EXCLUDED_MIME_TYPES = [
  "text/calendar",
  "application/ics",
  "text/vcard",
  "text/x-vcard",
  "image/gif",
];

const EXCLUDED_FILENAME_PATTERNS = [
  /^image$/i,
  /^image\.(png|jpg|jpeg|gif|webp)$/i,
  /^image\d+\.(png|jpg|jpeg|gif)$/i,
  /^photo\.(png|jpg|jpeg|gif|webp)$/i,
  /^attachment\.(pdf|docx?|xlsx?)$/i,
  /^document\.(pdf|docx?)$/i,
  /^file\.(pdf|docx?|xlsx?)$/i,
  /^untitled/i,
  /^screenshot\.(png|jpg)$/i,
];
