/**
 * Email Attachment Exfiltration to Drive
 *
 * Extracts email attachments and uploads to dated Drive folders
 * for unified search across Gmail and Drive.
 *
 * Folder structure: Email Attachments/YYYY-MM/filename.pdf
 *
 * User settings are in Config.gs - edit that file for your setup.
 * Filter patterns are in FilterConfig.gs - auto-generated, don't edit.
 */

// Internal constants (don't edit these)
const PROCESSED_IDS_KEY = 'processedMessageIds';
const CONTENT_HASHES_KEY = 'contentHashes';
const LAST_RUN_KEY = 'lastRun';
const PROCESSED_COUNT_KEY = 'processedCount';

/**
 * Retry a function with exponential backoff.
 * Logs detailed error info on each failure for debugging.
 * @param {Function} fn - Function to retry
 * @param {string} context - Description of what we're trying (for logs)
 * @param {number} maxAttempts - Maximum retry attempts (default: 3)
 * @param {number} initialDelayMs - Initial delay in ms (default: 1000)
 * @returns {*} Result of fn()
 */
function withRetry(fn, context, maxAttempts = 3, initialDelayMs = 1000) {
  let lastError;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      return fn();
    } catch (e) {
      lastError = e;
      // Log detailed error info
      console.error(`[Retry] ${context} attempt ${attempt}/${maxAttempts} failed:`);
      console.error(`  Error name: ${e.name}`);
      console.error(`  Error message: ${e.message}`);
      if (e.stack) {
        console.error(`  Stack: ${e.stack.split('\n')[0]}`);
      }

      if (attempt < maxAttempts) {
        const delay = initialDelayMs * Math.pow(2, attempt - 1);
        console.log(`  Retrying in ${delay}ms...`);
        Utilities.sleep(delay);
      }
    }
  }
  throw lastError;
}

/**
 * Get or create the root "Email Attachments" folder.
 * @returns {GoogleAppsScript.Drive.Folder}
 */
function getOrCreateRootFolder() {
  return withRetry(() => {
    const folders = DriveApp.getFoldersByName(ROOT_FOLDER_NAME);
    if (folders.hasNext()) {
      return folders.next();
    }
    console.log(`Creating root folder: ${ROOT_FOLDER_NAME}`);
    return DriveApp.createFolder(ROOT_FOLDER_NAME);
  }, 'getOrCreateRootFolder');
}

/**
 * Get or create a month folder (e.g., "2025-01") under root.
 * @param {Date} date - Date to determine month folder
 * @returns {GoogleAppsScript.Drive.Folder}
 */
function getOrCreateMonthFolder(date) {
  const root = getOrCreateRootFolder();
  const monthStr = Utilities.formatDate(date, 'UTC', 'yyyy-MM');

  return withRetry(() => {
    const folders = root.getFoldersByName(monthStr);
    if (folders.hasNext()) {
      return folders.next();
    }
    console.log(`Creating month folder: ${monthStr}`);
    return root.createFolder(monthStr);
  }, `getOrCreateMonthFolder(${monthStr})`);
}

/**
 * Build dedup state from files already in a Drive folder.
 * Replaces Properties-based dedup to avoid quota limits.
 *
 * @param {string} monthStr - Month folder name (YYYY-MM format)
 * @returns {{processedIds: Set<string>, contentHashes: Map<string, string>}}
 */
function buildDedupStateFromFolder(monthStr) {
  const processedIds = new Set();
  const contentHashes = new Map();

  const root = getOrCreateRootFolder();
  const folders = root.getFoldersByName(monthStr);

  if (!folders.hasNext()) {
    // Folder doesn't exist yet - no files to dedup against
    console.log(`[Dedup] No folder for ${monthStr}, starting fresh`);
    return { processedIds, contentHashes };
  }

  const folder = folders.next();
  const files = folder.getFiles();
  let fileCount = 0;

  while (files.hasNext()) {
    const file = files.next();
    const desc = file.getDescription() || '';
    const filename = file.getName();
    fileCount++;

    // Extract Message ID
    const idMatch = desc.match(/Message ID: (\w+)/);
    if (idMatch) {
      processedIds.add(idMatch[1]);
    }

    // Extract Content Hash
    const hashMatch = desc.match(/Content Hash: ([a-f0-9]+)/);
    if (hashMatch) {
      contentHashes.set(hashMatch[1], filename);
    }
  }

  console.log(`[Dedup] Found ${processedIds.size} message IDs and ${contentHashes.size} hashes from ${fileCount} files in ${monthStr}`);
  return { processedIds, contentHashes };
}

/**
 * Get in-flight processed IDs for a month (messages processed but not uploaded).
 * These are stored in Properties to survive across chunk boundaries.
 * Cleared when month advances.
 *
 * @param {string} monthStr - Month key (YYYY-MM format)
 * @returns {Set<string>}
 */
function getInflightProcessedIds(monthStr) {
  const props = PropertiesService.getScriptProperties();
  const key = `inflight_${monthStr}`;
  const stored = props.getProperty(key);
  if (!stored) return new Set();

  try {
    return new Set(JSON.parse(stored));
  } catch (e) {
    console.error(`Failed to parse in-flight IDs for ${monthStr}`);
    return new Set();
  }
}

/**
 * Save in-flight processed IDs for a month.
 * Only stores IDs not yet in Drive (processed but not uploaded).
 *
 * @param {string} monthStr - Month key (YYYY-MM format)
 * @param {Set<string>} ids - All processed IDs this month
 * @param {Set<string>} driveIds - IDs already in Drive (don't need to store)
 */
function saveInflightProcessedIds(monthStr, ids, driveIds) {
  const props = PropertiesService.getScriptProperties();
  const key = `inflight_${monthStr}`;

  // Only store IDs that aren't in Drive (those are "in-flight")
  const inflightOnly = Array.from(ids).filter(id => !driveIds.has(id));

  if (inflightOnly.length === 0) {
    // All IDs are in Drive, clear the in-flight key
    props.deleteProperty(key);
    return;
  }

  // Keep last 2000 to stay well under quota (these are current month only)
  const trimmed = inflightOnly.slice(-2000);
  props.setProperty(key, JSON.stringify(trimmed));
  console.log(`[Dedup] Saved ${trimmed.length} in-flight IDs for ${monthStr}`);
}

/**
 * Clear in-flight processed IDs for a month (called when month advances).
 *
 * @param {string} monthStr - Month key (YYYY-MM format)
 */
function clearInflightProcessedIds(monthStr) {
  const props = PropertiesService.getScriptProperties();
  const key = `inflight_${monthStr}`;
  props.deleteProperty(key);
  console.log(`[Dedup] Cleared in-flight IDs for ${monthStr}`);
}

/**
 * Build dedup state from Drive folders covering a date range.
 * For incremental processing (recent emails), scans current month and previous month.
 *
 * @param {string} sinceDate - Start date string (YYYY/MM/DD format)
 * @returns {{processedIds: Set<string>, contentHashes: Map<string, string>, driveIdsByMonth: Map<string, Set<string>>, processedIdsByMonth: Map<string, Set<string>>}}
 */
function buildDedupStateForDateRange(sinceDate) {
  const processedIds = new Set();
  const contentHashes = new Map();
  const driveIdsByMonth = new Map();  // Drive IDs per month (for filtering at save)
  const processedIdsByMonth = new Map();  // In-flight IDs per month (will grow during processing)

  // Parse the sinceDate to determine which months to scan
  const parts = sinceDate.split('/');
  const startYear = parseInt(parts[0], 10);
  const startMonth = parseInt(parts[1], 10);

  // Current date for end range
  const now = new Date();
  const endYear = now.getFullYear();
  const endMonth = now.getMonth() + 1;

  // Scan all months from start to end
  let year = startYear;
  let month = startMonth;
  const monthsScanned = [];

  while (year < endYear || (year === endYear && month <= endMonth)) {
    const monthStr = `${year}-${String(month).padStart(2, '0')}`;
    const driveState = buildDedupStateFromFolder(monthStr);
    const inflightIds = getInflightProcessedIds(monthStr);

    // Store Drive IDs for this month (needed for save later)
    driveIdsByMonth.set(monthStr, new Set(driveState.processedIds));

    // Store existing in-flight IDs for this month (will add new ones during processing)
    processedIdsByMonth.set(monthStr, new Set(inflightIds));

    // Merge Drive + in-flight into processedIds
    for (const id of driveState.processedIds) {
      processedIds.add(id);
    }
    for (const id of inflightIds) {
      processedIds.add(id);
    }
    for (const [hash, filename] of driveState.contentHashes) {
      contentHashes.set(hash, filename);
    }

    monthsScanned.push(monthStr);

    // Next month
    month++;
    if (month > 12) {
      month = 1;
      year++;
    }
  }

  const inflightCount = processedIds.size - Array.from(driveIdsByMonth.values()).reduce((sum, s) => sum + s.size, 0);
  console.log(`[Dedup] Scanned ${monthsScanned.length} months: ${monthsScanned.join(', ')}`);
  console.log(`[Dedup] Total: ${processedIds.size} message IDs (${inflightCount} in-flight), ${contentHashes.size} hashes`);

  return { processedIds, contentHashes, driveIdsByMonth, processedIdsByMonth };
}

/**
 * Compute MD5 hash of blob content.
 * @param {GoogleAppsScript.Base.Blob} blob
 * @returns {string} hex hash
 */
function computeHash(blob) {
  const digest = Utilities.computeDigest(Utilities.DigestAlgorithm.MD5, blob.getBytes());
  return digest.map(b => ('0' + ((b < 0 ? b + 256 : b).toString(16))).slice(-2)).join('');
}

/**
 * Update processing stats.
 * @param {number} count - Number of messages processed this run
 */
function updateStats(count) {
  const props = PropertiesService.getScriptProperties();
  const current = parseInt(props.getProperty(PROCESSED_COUNT_KEY) || '0', 10);
  props.setProperty(PROCESSED_COUNT_KEY, String(current + count));
  props.setProperty(LAST_RUN_KEY, new Date().toISOString());
}

/**
 * Get processing stats.
 * @returns {{lastRun: string|null, processedCount: number}}
 */
function getStats() {
  const props = PropertiesService.getScriptProperties();
  return {
    lastRun: props.getProperty(LAST_RUN_KEY),
    processedCount: parseInt(props.getProperty(PROCESSED_COUNT_KEY) || '0', 10)
  };
}

/**
 * Extract Google Drive/Docs file IDs from text.
 * Mirrors the Python _extract_drive_links() logic.
 * @param {string} text - Email body text
 * @returns {Array<{fileId: string, url: string}>}
 */
function extractDriveLinks(text) {
  if (!text) return [];

  const patterns = [
    // drive.google.com/open?id=XXX
    /https?:\/\/drive\.google\.com\/open\?id=([a-zA-Z0-9_-]+)/g,
    // drive.google.com/file/d/XXX
    /https?:\/\/drive\.google\.com\/file\/d\/([a-zA-Z0-9_-]+)/g,
    // drive.google.com/drive/folders/XXX
    /https?:\/\/drive\.google\.com\/drive\/folders\/([a-zA-Z0-9_-]+)/g,
    // docs.google.com/document/d/XXX
    /https?:\/\/docs\.google\.com\/document\/d\/([a-zA-Z0-9_-]+)/g,
    // docs.google.com/spreadsheets/d/XXX
    /https?:\/\/docs\.google\.com\/spreadsheets\/d\/([a-zA-Z0-9_-]+)/g,
    // docs.google.com/presentation/d/XXX
    /https?:\/\/docs\.google\.com\/presentation\/d\/([a-zA-Z0-9_-]+)/g,
    // docs.google.com/forms/d/XXX
    /https?:\/\/docs\.google\.com\/forms\/d\/([a-zA-Z0-9_-]+)/g,
  ];

  const seen = new Set();
  const results = [];

  for (const pattern of patterns) {
    let match;
    while ((match = pattern.exec(text)) !== null) {
      const fileId = match[1];
      if (!seen.has(fileId)) {
        seen.add(fileId);
        results.push({ fileId, url: match[0] });
      }
    }
  }

  return results;
}

/**
 * Create a Drive shortcut to a file in the specified folder.
 * If we can't read the target file's metadata (permission denied, deleted),
 * we still create the shortcut with a fallback name — Drive will handle
 * access checks when the user clicks it.
 *
 * @param {string} targetFileId - ID of file to create shortcut to
 * @param {GoogleAppsScript.Drive.Folder} folder - Destination folder
 * @param {Date} emailDate - Email date for modifiedTime
 * @param {string} messageId - Gmail message ID for description
 * @returns {{success: boolean, name?: string, error?: string}}
 */
function createDriveShortcut(targetFileId, folder, emailDate, messageId) {
  let shortcutName;

  // Try to get the file's actual name (best case)
  // supportsAllDrives required for Shared Drive files
  try {
    const targetFile = Drive.Files.get(targetFileId, { fields: 'name', supportsAllDrives: true });
    shortcutName = targetFile.name;
  } catch (e) {
    // Can't read file metadata — use fallback name
    // Shortcut will still work if user has access; Drive handles permission check
    shortcutName = `Link-${targetFileId.substring(0, 12)}`;
  }

  // Check if shortcut already exists in folder
  const existing = folder.getFilesByName(shortcutName);
  if (existing.hasNext()) {
    return { success: false, error: 'exists', name: shortcutName };
  }

  try {
    // Create shortcut — even if we couldn't read metadata, the shortcut may work
    // supportsAllDrives required for Shared Drive files
    const shortcutMetadata = {
      name: shortcutName,
      mimeType: 'application/vnd.google-apps.shortcut',
      shortcutDetails: {
        targetId: targetFileId
      },
      parents: [folder.getId()],
      description: `Linked from email. Message ID: ${messageId}`,
      modifiedTime: emailDate.toISOString()
    };

    Drive.Files.create(shortcutMetadata, null, { supportsAllDrives: true });
    return { success: true, name: shortcutName };

  } catch (e) {
    // Shortcut creation itself failed (rare — usually target access issues)
    return { success: false, error: e.message };
  }
}

/**
 * Check if an attachment should be skipped (trivial artifact).
 * @param {GoogleAppsScript.Gmail.GmailAttachment} attachment
 * @returns {boolean}
 */
function isTrivialAttachment(attachment) {
  const name = attachment.getName().toLowerCase().trim();
  const mime = attachment.getContentType();
  const size = attachment.getSize();

  // Empty filename - skip
  if (!name || name === '') {
    return true;
  }

  // Check excluded MIME types (from FilterConfig.gs)
  if (EXCLUDED_MIME_TYPES.includes(mime)) {
    return true;
  }

  // Check excluded filename patterns (from FilterConfig.gs)
  for (const pattern of EXCLUDED_FILENAME_PATTERNS) {
    if (pattern.test(name)) {
      return true;
    }
  }

  // Images below threshold (logos, signatures, inline graphics)
  // IMAGE_SIZE_THRESHOLD from FilterConfig.gs
  if (mime.startsWith('image/') && size < IMAGE_SIZE_THRESHOLD) {
    return true;
  }

  return false;
}

/**
 * Check if a filename is generic and needs prefixing.
 *
 * NOTE: Most generic patterns (image.png, photo.jpg, etc.) are filtered out
 * by isTrivialAttachment() before reaching this function. This only catches
 * patterns we DON'T want to filter but DO want to rename for clarity.
 *
 * @param {string} filename
 * @returns {boolean}
 */
function isGenericFilename(filename) {
  const lower = filename.toLowerCase();
  const genericPatterns = [
    // UUID pattern: 8-4-4-4-12 hex chars (with optional extension)
    // These are kept but renamed for human readability
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(\.[a-z]+)?$/,
  ];
  return genericPatterns.some(p => p.test(lower));
}

/**
 * Infer file extension from MIME type.
 * @param {string} mimeType
 * @returns {string} extension including dot, or empty string
 */
function inferExtension(mimeType) {
  const mimeToExt = {
    'image/png': '.png',
    'image/jpeg': '.jpg',
    'image/gif': '.gif',
    'image/webp': '.webp',
    'application/pdf': '.pdf',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': '.pptx',
    'application/msword': '.doc',
    'application/vnd.ms-excel': '.xls',
    'application/vnd.ms-powerpoint': '.ppt',
    'text/plain': '.txt',
    'text/html': '.html',
    'text/csv': '.csv',
  };
  return mimeToExt[mimeType] || '';
}

/**
 * Ensure filename has an extension, inferring from MIME if needed.
 * @param {string} filename
 * @param {string} mimeType
 * @returns {string}
 */
function ensureExtension(filename, mimeType) {
  // Check if already has extension
  if (/\.[a-zA-Z0-9]+$/.test(filename)) {
    return filename;
  }
  const ext = inferExtension(mimeType);
  return filename + ext;
}

/**
 * Generate a smart filename with context for generic names.
 * @param {string} originalName
 * @param {Date} date
 * @param {string} sender - email address
 * @returns {string}
 */
function smartFilename(originalName, date, sender) {
  if (!isGenericFilename(originalName)) {
    return originalName;
  }

  const dateStr = Utilities.formatDate(date, 'UTC', 'yyyy-MM-dd');
  // Extract username from email (before @)
  const senderName = sender.split('@')[0].replace(/[^a-zA-Z0-9]/g, '_').substring(0, 20);

  // Split filename and extension
  const lastDot = originalName.lastIndexOf('.');
  if (lastDot === -1) {
    return `${dateStr}_${senderName}_${originalName}`;
  }

  const base = originalName.substring(0, lastDot);
  const ext = originalName.substring(lastDot);
  return `${dateStr}_${senderName}_${base}${ext}`;
}

/**
 * Build metadata description for uploaded file.
 * @param {GoogleAppsScript.Gmail.GmailMessage} message
 * @param {string} contentHash
 * @returns {string}
 */
function buildDescription(message, contentHash) {
  const from = message.getFrom();
  const subject = message.getSubject();
  const date = message.getDate().toISOString();
  const messageId = message.getId();

  return [
    `From: ${from}`,
    `Subject: ${subject}`,
    `Date: ${date}`,
    `Message ID: ${messageId}`,
    `Content Hash: ${contentHash}`,
  ].join('\n');
}

/**
 * Process a single message: extract attachments and Drive links, upload to Drive.
 * @param {GoogleAppsScript.Gmail.GmailMessage} message
 * @param {Map<string, string>} contentHashes - existing hashes
 * @param {boolean} dryRun - if true, don't actually upload
 * @param {Set<string>} [attemptedFileIds] - file IDs already attempted this chunk (avoids repeated API calls for deleted files)
 * @returns {{uploaded: number, skipped: number, errors: number, shortcuts: number, newHashes: Array}}
 */
function processMessage(message, contentHashes, dryRun = false, attemptedFileIds = null) {
  const attachments = message.getAttachments();
  const messageDate = message.getDate();
  const sender = message.getFrom();
  const messageId = message.getId();
  const folder = dryRun ? null : getOrCreateMonthFolder(messageDate);

  let uploaded = 0;
  let skipped = 0;
  let errors = 0;
  let shortcuts = 0;
  let shortcutsFailed = 0;  // Permission denied, file deleted, etc.
  const newHashes = [];

  for (const attachment of attachments) {
    try {
      if (isTrivialAttachment(attachment)) {
        skipped++;
        continue;
      }

      const originalName = attachment.getName();
      const mimeType = attachment.getContentType();
      const blob = attachment.copyBlob();
      const hash = computeHash(blob);

      // Check for duplicate content (even with different filename)
      if (contentHashes.has(hash)) {
        skipped++;
        continue;
      }

      // Ensure extension exists, then apply smart naming
      const withExtension = ensureExtension(originalName, mimeType);
      const filename = smartFilename(withExtension, messageDate, sender);

      if (dryRun) {
        console.log(`  → ${filename}`);
        uploaded++;
        newHashes.push([hash, filename]);
        continue;
      }

      // Log filename in real runs too
      console.log(`  → ${filename}`);

      // Check if file already exists in folder by name (belt and suspenders)
      const existing = folder.getFilesByName(filename);
      if (existing.hasNext()) {
        skipped++;
        continue;
      }

      // Upload with metadata including original email date
      // Note: createdTime is NOT writable via API, only modifiedTime
      const description = buildDescription(message, hash);
      const fileMetadata = {
        name: filename,
        parents: [folder.getId()],
        description: description,
        modifiedTime: messageDate.toISOString()
      };
      Drive.Files.create(fileMetadata, blob);
      uploaded++;
      newHashes.push([hash, filename]);

    } catch (e) {
      console.error(`Error processing attachment ${attachment.getName()}: ${e.message}`);
      errors++;
    }
  }

  // Process Drive links in message body
  // Get body (prefer plain text, fall back to HTML)
  const body = message.getPlainBody() || message.getBody() || '';
  const driveLinks = extractDriveLinks(body);

  for (const link of driveLinks) {
    try {
      // Skip if we've already attempted this file ID this chunk (avoids repeated API calls for deleted files)
      if (attemptedFileIds && attemptedFileIds.has(link.fileId)) {
        continue;
      }

      if (dryRun) {
        console.log(`  ↗ [shortcut] ${link.fileId}`);
        shortcuts++;
        if (attemptedFileIds) attemptedFileIds.add(link.fileId);
        continue;
      }

      const result = createDriveShortcut(link.fileId, folder, messageDate, messageId);
      if (attemptedFileIds) attemptedFileIds.add(link.fileId);

      if (result.success) {
        console.log(`  ↗ ${result.name}`);
        shortcuts++;
      } else if (result.error === 'exists') {
        // Shortcut already exists, not an error
        skipped++;
      } else {
        // File might be deleted, inaccessible, or permission denied
        console.log(`  ↗ [skipped] ${link.fileId}: ${result.error}`);
        shortcutsFailed++;
      }
    } catch (e) {
      console.error(`Error creating shortcut for ${link.fileId}: ${e.message}`);
      if (attemptedFileIds) attemptedFileIds.add(link.fileId);
      shortcutsFailed++;
    }
  }

  return { uploaded, skipped, errors, shortcuts, shortcutsFailed, newHashes };
}

/**
 * Process emails with attachments since a given date.
 * @param {string} sinceDate - Date string (e.g., "2024-01-01")
 * @param {number} [maxMessages=100] - Maximum messages to process per run
 * @param {boolean} [dryRun=false] - If true, don't actually upload
 * @returns {{processed: number, uploaded: number, skipped: number, errors: number, alreadyDone: number}}
 */
/**
 * Build Gmail query with exclusions from config.
 * @param {string} sinceDate
 * @returns {string}
 */
function buildSearchQuery(sinceDate) {
  let query = `has:attachment after:${sinceDate}`;

  // Add subject exclusions
  if (EXCLUDED_SUBJECT_PATTERNS.length > 0) {
    const subjectExclusions = EXCLUDED_SUBJECT_PATTERNS
      .map(p => p.includes(' ') ? `"${p}"` : p)
      .join(' OR ');
    query += ` -subject:(${subjectExclusions})`;
  }

  return query;
}

/**
 * Check if a message should be skipped based on TO:/FROM: filters.
 * @param {GoogleAppsScript.Gmail.GmailMessage} message
 * @returns {boolean}
 */
function shouldSkipMessage(message) {
  // Check FROM: exclusions
  if (EXCLUDED_FROM_ADDRESSES.length > 0) {
    const from = message.getFrom().toLowerCase();
    for (const excluded of EXCLUDED_FROM_ADDRESSES) {
      if (from.includes(excluded.toLowerCase())) {
        return true;
      }
    }
  }

  // Check TO: exclusions
  if (EXCLUDED_TO_ADDRESSES.length > 0) {
    const to = message.getTo().toLowerCase();
    for (const excluded of EXCLUDED_TO_ADDRESSES) {
      if (to.includes(excluded.toLowerCase())) {
        return true;
      }
    }
  }

  return false;
}

function backfillSince(sinceDate, maxMessages = 100, dryRun = false) {
  const query = buildSearchQuery(sinceDate);
  console.log(`Searching: ${query}${dryRun ? ' [DRY RUN]' : ''}`);

  const threads = GmailApp.search(query, 0, maxMessages);
  console.log(`Found ${threads.length} threads`);

  // Build dedup state from Drive folders covering the date range
  // Now includes in-flight IDs and returns driveIdsByMonth for save
  const { processedIds, contentHashes, driveIdsByMonth, processedIdsByMonth } = buildDedupStateForDateRange(sinceDate);

  // Track attempted file IDs to avoid repeated API calls for deleted/inaccessible files
  const attemptedFileIds = new Set();

  let processed = 0;
  let totalUploaded = 0;
  let totalSkipped = 0;
  let totalErrors = 0;
  let totalShortcuts = 0;
  let totalShortcutsFailed = 0;
  let alreadyDone = 0;

  for (const thread of threads) {
    const messages = thread.getMessages();

    for (const message of messages) {
      const messageId = message.getId();

      if (processedIds.has(messageId)) {
        alreadyDone++;
        continue;
      }

      const attachments = message.getAttachments();
      if (attachments.length === 0) continue;

      // Check TO:/FROM: exclusions
      if (shouldSkipMessage(message)) {
        continue;
      }

      const result = processMessage(message, contentHashes, dryRun, attemptedFileIds);
      totalUploaded += result.uploaded;
      totalSkipped += result.skipped;
      totalErrors += result.errors;
      totalShortcuts += result.shortcuts;
      totalShortcutsFailed += result.shortcutsFailed;

      // Track new hashes
      for (const [hash, filename] of result.newHashes) {
        contentHashes.set(hash, filename);
      }

      if (!dryRun) {
        processedIds.add(messageId);

        // Track by month for in-flight save
        const msgDate = message.getDate();
        const monthStr = `${msgDate.getFullYear()}-${String(msgDate.getMonth() + 1).padStart(2, '0')}`;
        if (!processedIdsByMonth.has(monthStr)) {
          processedIdsByMonth.set(monthStr, new Set());
        }
        processedIdsByMonth.get(monthStr).add(messageId);
      }
      processed++;
    }
  }

  if (!dryRun) {
    // Save in-flight IDs for each affected month
    for (const [monthStr, monthProcessedIds] of processedIdsByMonth) {
      if (monthProcessedIds.size > 0) {
        const driveIds = driveIdsByMonth.get(monthStr) || new Set();
        saveInflightProcessedIds(monthStr, monthProcessedIds, driveIds);
      }
    }
    updateStats(processed);
  }

  const stats = {
    processed,
    uploaded: totalUploaded,
    shortcuts: totalShortcuts,
    shortcutsFailed: totalShortcutsFailed,
    skipped: totalSkipped,
    errors: totalErrors,
    alreadyDone,
    dryRun
  };

  console.log(`Complete: ${JSON.stringify(stats)}`);
  return stats;
}

/**
 * Process new emails since last run.
 * Called by time-driven trigger.
 */
function processNewEmails() {
  console.log(`Using filter config v${FILTER_CONFIG_VERSION}`);
  const props = PropertiesService.getScriptProperties();
  const lastRun = props.getProperty(LAST_RUN_KEY);

  let sinceDate;
  if (lastRun) {
    // Convert ISO date to Gmail search format (YYYY/MM/DD)
    const date = new Date(lastRun);
    sinceDate = Utilities.formatDate(date, 'UTC', 'yyyy/MM/dd');
  } else {
    // First run - process last 7 days
    const date = new Date();
    date.setDate(date.getDate() - 7);
    sinceDate = Utilities.formatDate(date, 'UTC', 'yyyy/MM/dd');
  }

  console.log(`Processing emails since: ${sinceDate}`);
  return backfillSince(sinceDate, 50, false);
}

/**
 * Manual test function - process last 30 days (dry run).
 */
function testBackfillDryRun() {
  const date = new Date();
  date.setDate(date.getDate() - 30);
  const sinceDate = Utilities.formatDate(date, 'UTC', 'yyyy/MM/dd');
  return backfillSince(sinceDate, 20, true);
}

/**
 * Manual test function - process last 30 days (real).
 */
function testBackfill() {
  const date = new Date();
  date.setDate(date.getDate() - 30);
  const sinceDate = Utilities.formatDate(date, 'UTC', 'yyyy/MM/dd');
  return backfillSince(sinceDate, 20, false);
}

/**
 * Process 2 months of email.
 */
function backfill2Months() {
  const date = new Date();
  date.setMonth(date.getMonth() - 2);
  const sinceDate = Utilities.formatDate(date, 'UTC', 'yyyy/MM/dd');
  return backfillSince(sinceDate, 200, false);
}

/**
 * Process 5 months of email.
 */
function backfill5Months() {
  const date = new Date();
  date.setMonth(date.getMonth() - 5);
  const sinceDate = Utilities.formatDate(date, 'UTC', 'yyyy/MM/dd');
  return backfillSince(sinceDate, 500, false);
}

/**
 * Dry run 2 months - see what would happen.
 */
function dryRun2Months() {
  const date = new Date();
  date.setMonth(date.getMonth() - 2);
  const sinceDate = Utilities.formatDate(date, 'UTC', 'yyyy/MM/dd');
  return backfillSince(sinceDate, 200, true);
}

/**
 * Dry run 5 months - see what would happen.
 */
function dryRun5Months() {
  const date = new Date();
  date.setMonth(date.getMonth() - 5);
  const sinceDate = Utilities.formatDate(date, 'UTC', 'yyyy/MM/dd');
  return backfillSince(sinceDate, 500, true);
}

/**
 * Dry run 1 year - see what would happen.
 */
function dryRun1Year() {
  const date = new Date();
  date.setFullYear(date.getFullYear() - 1);
  const sinceDate = Utilities.formatDate(date, 'UTC', 'yyyy/MM/dd');
  return backfillSince(sinceDate, 500, true);  // Gmail API max is 500
}

/**
 * Dry run for a specific date range.
 * Edit the dates below and run from editor.
 */
function dryRunDateRange() {
  const startDate = '2024/10/01';  // Edit this
  const endDate = '2024/12/01';    // Edit this (exclusive)
  const query = buildSearchQuery(startDate) + ` before:${endDate}`;
  console.log(`Searching: ${query} [DRY RUN]`);

  const threads = GmailApp.search(query, 0, 500);
  console.log(`Found ${threads.length} threads`);

  // Build dedup state from Drive folders
  const { processedIds, contentHashes } = buildDedupStateForDateRange(startDate);

  let processed = 0;
  let totalUploaded = 0;
  let totalSkipped = 0;
  let totalErrors = 0;
  let totalShortcuts = 0;
  let totalShortcutsFailed = 0;

  for (const thread of threads) {
    const messages = thread.getMessages();
    for (const message of messages) {
      const messageId = message.getId();
      if (processedIds.has(messageId)) continue;

      const attachments = message.getAttachments();
      if (attachments.length === 0) continue;
      if (shouldSkipMessage(message)) continue;

      const result = processMessage(message, contentHashes, true);
      totalUploaded += result.uploaded;
      totalSkipped += result.skipped;
      totalErrors += result.errors;
      totalShortcuts += result.shortcuts;
      totalShortcutsFailed += result.shortcutsFailed;

      for (const [hash, filename] of result.newHashes) {
        contentHashes.set(hash, filename);
      }
      processed++;
    }
  }

  const stats = { processed, uploaded: totalUploaded, shortcuts: totalShortcuts, shortcutsFailed: totalShortcutsFailed, skipped: totalSkipped, errors: totalErrors };
  console.log(`Complete: ${JSON.stringify(stats)}`);
  return stats;
}

// ============================================================================
// CHUNK-BASED PROCESSING (for time triggers)
// ============================================================================
// These functions process small batches and exit, designed for 15-min triggers.
// CHUNK_SIZE is defined in Config.gs

/**
 * Process a chunk of messages for a given year.
 * Acquires lock, processes up to CHUNK_SIZE messages, releases lock.
 * Designed for time-triggered execution.
 *
 * @param {number} year - Year to process
 * @returns {{status: string, year: number, month: number, processed: number, uploaded: number, skipped: number, moreWork: boolean}}
 */
function processYearChunk(year) {
  console.log(`Using filter config v${FILTER_CONFIG_VERSION}`);
  // No lock - each year has its own processedIds, so parallel execution is safe.
  // contentHashes is shared but duplicates are harmless (just extra files in Drive).

  const props = PropertiesService.getScriptProperties();
  const checkpointKey = `backfill_${year}_month`;
  const offsetKey = `backfill_${year}_offset`;

  // Resume from checkpoint
  const month = parseInt(props.getProperty(checkpointKey) || '1', 10);
  const offset = parseInt(props.getProperty(offsetKey) || '0', 10);

  // Check if year is complete
  if (month > 12) {
    console.log(`[${year}] Year complete, nothing to do`);
    return { status: 'complete', year, moreWork: false };
  }

  console.log(`[${year}] Processing month ${month}, offset ${offset}`);

  // Build query for this month
  const startDate = `${year}/${String(month).padStart(2, '0')}/01`;
  let endYear = year;
  let endMonth = month + 1;
  if (endMonth > 12) {
    endMonth = 1;
    endYear = year + 1;
  }
  const endDate = `${endYear}/${String(endMonth).padStart(2, '0')}/01`;
  const query = buildSearchQuery(startDate) + ` before:${endDate}`;

  // Search from current offset
  const threads = GmailApp.search(query, offset, 100);  // Fetch 100 threads, process up to CHUNK_SIZE messages

  if (threads.length === 0) {
    // Month complete, advance to next
    const completedMonthStr = `${year}-${String(month).padStart(2, '0')}`;
    clearInflightProcessedIds(completedMonthStr);  // Clear in-flight IDs for completed month

    console.log(`[${year}] Month ${month} complete, advancing to month ${month + 1}`);
    props.setProperty(checkpointKey, String(month + 1));
    props.deleteProperty(offsetKey);

    if (month + 1 > 12) {
      console.log(`[${year}] Year complete!`);
      return { status: 'year_complete', year, month, moreWork: false };
    }
    return { status: 'month_complete', year, month, moreWork: true };
  }

  console.log(`[${year}] Found ${threads.length} threads at offset ${offset}`);

  // Build dedup state from Drive folder + in-flight IDs from Properties
  const monthStr = `${year}-${String(month).padStart(2, '0')}`;
  const driveState = buildDedupStateFromFolder(monthStr);
  const driveIds = new Set(driveState.processedIds);  // Keep reference to Drive-only IDs
  const inflightIds = getInflightProcessedIds(monthStr);

  // Merge: Drive IDs + in-flight IDs = complete set of processed messages
  const processedIds = new Set([...driveState.processedIds, ...inflightIds]);
  const contentHashes = driveState.contentHashes;

  if (inflightIds.size > 0) {
    console.log(`[Dedup] Loaded ${inflightIds.size} in-flight IDs for ${monthStr}`);
  }

  // Track attempted file IDs to avoid repeated API calls for deleted/inaccessible files
  const attemptedFileIds = new Set();

  let chunkProcessed = 0;
  let chunkUploaded = 0;
  let chunkSkipped = 0;
  let chunkErrors = 0;
  let chunkShortcuts = 0;
  let chunkShortcutsFailed = 0;
  let threadsConsumed = 0;
  let dedupSkipped = 0;  // DEBUG: count messages skipped via dedup

  // Process threads until we hit CHUNK_SIZE messages
  for (const thread of threads) {
    if (chunkProcessed >= CHUNK_SIZE) {
      break;
    }

    const messages = thread.getMessages();
    let stoppedEarly = false;

    for (const message of messages) {
      if (chunkProcessed >= CHUNK_SIZE) {
        stoppedEarly = true;
        break;
      }

      const messageId = message.getId();

      if (processedIds.has(messageId)) {
        dedupSkipped++;  // DEBUG
        continue;
      }

      const attachments = message.getAttachments();
      if (attachments.length === 0) continue;

      if (shouldSkipMessage(message)) {
        continue;
      }

      const result = processMessage(message, contentHashes, false, attemptedFileIds);
      chunkUploaded += result.uploaded;
      chunkSkipped += result.skipped;
      chunkErrors += result.errors;
      chunkShortcuts += result.shortcuts;
      chunkShortcutsFailed += result.shortcutsFailed;

      for (const [hash, filename] of result.newHashes) {
        contentHashes.set(hash, filename);
      }

      processedIds.add(messageId);
      chunkProcessed++;
    }

    // Only count thread as consumed if we finished all its messages
    // (prevents skipping partially-processed threads on restart)
    if (!stoppedEarly) {
      threadsConsumed++;
    }
  }

  // Save in-flight IDs (messages processed but not uploaded to Drive)
  saveInflightProcessedIds(monthStr, processedIds, driveIds);

  // Update offset for next run
  const newOffset = offset + threadsConsumed;
  props.setProperty(offsetKey, String(newOffset));

  // Check if we exhausted this batch (might be more threads)
  const moreInMonth = threads.length >= 100;

  console.log(`[${year}] Chunk complete: ${chunkUploaded} uploaded, ${chunkShortcuts} shortcuts (${chunkShortcutsFailed} failed), ${chunkSkipped} att-skipped, ${dedupSkipped} dedup-skipped, ${chunkProcessed} processed, ${threadsConsumed} threads consumed, offset now ${newOffset}`);

  updateStats(chunkProcessed);

  return {
    status: 'chunk_complete',
    year,
    month,
    offset: newOffset,
    processed: chunkProcessed,
    uploaded: chunkUploaded,
    shortcuts: chunkShortcuts,
    shortcutsFailed: chunkShortcutsFailed,
    skipped: chunkSkipped,
    errors: chunkErrors,
    moreWork: moreInMonth || month < 12
  };
}

/**
 * Process chunks for all backfill years. Safe to call from time trigger.
 * Uses BACKFILL_YEARS from Config.gs.
 */
function chunkBackfillAll() {
  const results = [];
  for (const year of BACKFILL_YEARS) {
    results.push(processYearChunk(year));
  }
  return results;
}

/**
 * Process chunk for a specific year (for manual runs).
 * @param {number} year
 */
function chunkYear(year) {
  return processYearChunk(year);
}

/**
 * Reset offset for a year (to re-scan current month without losing month progress).
 * @param {number} year
 */
function resetOffset(year) {
  PropertiesService.getScriptProperties().deleteProperty(`backfill_${year}_offset`);
  console.log(`Offset for ${year} cleared (will re-scan current month)`);
}

// ============================================================================
// DRIVE LINKS BACKFILL (for emails with Drive links but no attachments)
// ============================================================================
// The main backfill uses `has:attachment` which misses emails that share
// Drive/Docs links without file attachments. This sweep catches those.

/**
 * Process Drive links from a single message (no attachment handling).
 * @param {GoogleAppsScript.Gmail.GmailMessage} message
 * @param {GoogleAppsScript.Drive.Folder} folder - Month folder for shortcuts
 * @param {boolean} dryRun
 * @param {Set<string>} [attemptedFileIds] - file IDs already attempted this chunk
 * @returns {{shortcuts: number, skipped: number, errors: number}}
 */
function processDriveLinksOnly(message, folder, dryRun = false, attemptedFileIds = null) {
  const messageId = message.getId();
  const messageDate = message.getDate();

  let shortcuts = 0;
  let skipped = 0;
  let errors = 0;

  // Get body (prefer plain text, fall back to HTML)
  const body = message.getPlainBody() || message.getBody() || '';
  const driveLinks = extractDriveLinks(body);

  if (driveLinks.length === 0) {
    return { shortcuts: 0, skipped: 0, errors: 0 };
  }

  for (const link of driveLinks) {
    try {
      // Skip if we've already attempted this file ID this chunk
      if (attemptedFileIds && attemptedFileIds.has(link.fileId)) {
        continue;
      }

      if (dryRun) {
        console.log(`  ↗ [shortcut] ${link.fileId}`);
        shortcuts++;
        if (attemptedFileIds) attemptedFileIds.add(link.fileId);
        continue;
      }

      const result = createDriveShortcut(link.fileId, folder, messageDate, messageId);
      if (attemptedFileIds) attemptedFileIds.add(link.fileId);

      if (result.success) {
        console.log(`  ↗ ${result.name}`);
        shortcuts++;
      } else if (result.error === 'exists') {
        skipped++;
      } else {
        // File might be deleted, inaccessible, or permission denied
        console.log(`  ↗ [skipped] ${link.fileId}: ${result.error}`);
        errors++;
      }
    } catch (e) {
      console.error(`Error creating shortcut for ${link.fileId}: ${e.message}`);
      if (attemptedFileIds) attemptedFileIds.add(link.fileId);
      errors++;
    }
  }

  return { shortcuts, skipped, errors };
}

/**
 * Backfill Drive links from emails that don't have attachments.
 * Catches emails that share Google Docs/Drive links in the body.
 *
 * @param {string} sinceDate - Date string (e.g., "2024/01/01")
 * @param {number} [maxMessages=100] - Maximum messages to process
 * @param {boolean} [dryRun=false] - If true, don't actually create shortcuts
 * @returns {{processed: number, shortcuts: number, skipped: number, errors: number, alreadyDone: number}}
 */
function backfillDriveLinks(sinceDate, maxMessages = 100, dryRun = false) {
  // Query for emails mentioning Drive URLs (but NOT requiring attachments)
  // Exclude emails with attachments since main backfill handles those
  const query = `after:${sinceDate} -has:attachment (drive.google.com OR docs.google.com)`;
  console.log(`Searching: ${query}${dryRun ? ' [DRY RUN]' : ''}`);

  const threads = GmailApp.search(query, 0, maxMessages);
  console.log(`Found ${threads.length} threads`);

  // Build dedup state - reuse existing infrastructure
  const { processedIds } = buildDedupStateForDateRange(sinceDate);

  // Track attempted file IDs to avoid repeated API calls
  const attemptedFileIds = new Set();

  let processed = 0;
  let totalShortcuts = 0;
  let totalSkipped = 0;
  let totalErrors = 0;
  let alreadyDone = 0;

  for (const thread of threads) {
    const messages = thread.getMessages();

    for (const message of messages) {
      const messageId = message.getId();

      if (processedIds.has(messageId)) {
        alreadyDone++;
        continue;
      }

      // Skip messages based on TO:/FROM: exclusions
      if (shouldSkipMessage(message)) {
        continue;
      }

      const messageDate = message.getDate();
      const folder = dryRun ? null : getOrCreateMonthFolder(messageDate);

      const result = processDriveLinksOnly(message, folder, dryRun, attemptedFileIds);

      if (result.shortcuts > 0 || result.errors > 0) {
        // Only count as processed if we found Drive links
        totalShortcuts += result.shortcuts;
        totalSkipped += result.skipped;
        totalErrors += result.errors;
        processed++;
      }
    }
  }

  const stats = {
    processed,
    shortcuts: totalShortcuts,
    skipped: totalSkipped,
    errors: totalErrors,
    alreadyDone,
    dryRun
  };

  console.log(`Complete: ${JSON.stringify(stats)}`);
  return stats;
}

/**
 * Process a chunk of Drive-link-only emails for a given year.
 * Designed for time-triggered execution.
 *
 * @param {number} year - Year to process
 * @returns {{status: string, year: number, month: number, processed: number, shortcuts: number, moreWork: boolean}}
 */
function processDriveLinksYearChunk(year) {
  const props = PropertiesService.getScriptProperties();
  const checkpointKey = `drivelinks_${year}_month`;
  const offsetKey = `drivelinks_${year}_offset`;

  // Resume from checkpoint
  const month = parseInt(props.getProperty(checkpointKey) || '1', 10);
  const offset = parseInt(props.getProperty(offsetKey) || '0', 10);

  // Check if year is complete
  if (month > 12) {
    console.log(`[DriveLinks ${year}] Year complete, nothing to do`);
    return { status: 'complete', year, moreWork: false };
  }

  console.log(`[DriveLinks ${year}] Processing month ${month}, offset ${offset}`);

  // Build query for this month - Drive links WITHOUT attachments
  const startDate = `${year}/${String(month).padStart(2, '0')}/01`;
  let endYear = year;
  let endMonth = month + 1;
  if (endMonth > 12) {
    endMonth = 1;
    endYear = year + 1;
  }
  const endDate = `${endYear}/${String(endMonth).padStart(2, '0')}/01`;
  const query = `after:${startDate} before:${endDate} -has:attachment (drive.google.com OR docs.google.com)`;

  // Search from current offset
  const threads = GmailApp.search(query, offset, 100);

  if (threads.length === 0) {
    // Month complete, advance to next
    console.log(`[DriveLinks ${year}] Month ${month} complete, advancing to month ${month + 1}`);
    props.setProperty(checkpointKey, String(month + 1));
    props.deleteProperty(offsetKey);

    if (month + 1 > 12) {
      console.log(`[DriveLinks ${year}] Year complete!`);
      return { status: 'year_complete', year, month, moreWork: false };
    }
    return { status: 'month_complete', year, month, moreWork: true };
  }

  console.log(`[DriveLinks ${year}] Found ${threads.length} threads at offset ${offset}`);

  // Build dedup state from Drive folder
  const monthStr = `${year}-${String(month).padStart(2, '0')}`;
  const { processedIds } = buildDedupStateFromFolder(monthStr);

  // Track attempted file IDs to avoid repeated API calls
  const attemptedFileIds = new Set();

  let chunkProcessed = 0;
  let chunkShortcuts = 0;
  let chunkSkipped = 0;
  let chunkErrors = 0;
  let threadsConsumed = 0;

  const folder = getOrCreateMonthFolder(new Date(year, month - 1, 1));

  // Process threads until we hit CHUNK_SIZE
  for (const thread of threads) {
    if (chunkProcessed >= CHUNK_SIZE) {
      break;
    }

    const messages = thread.getMessages();
    let stoppedEarly = false;

    for (const message of messages) {
      if (chunkProcessed >= CHUNK_SIZE) {
        stoppedEarly = true;
        break;
      }

      const messageId = message.getId();

      if (processedIds.has(messageId)) {
        continue;
      }

      if (shouldSkipMessage(message)) {
        continue;
      }

      const result = processDriveLinksOnly(message, folder, false, attemptedFileIds);

      if (result.shortcuts > 0 || result.errors > 0) {
        chunkShortcuts += result.shortcuts;
        chunkSkipped += result.skipped;
        chunkErrors += result.errors;
        chunkProcessed++;
      }
    }

    if (!stoppedEarly) {
      threadsConsumed++;
    }
  }

  // Update offset for next run
  const newOffset = offset + threadsConsumed;
  props.setProperty(offsetKey, String(newOffset));

  const moreInMonth = threads.length >= 100;

  console.log(`[DriveLinks ${year}] Chunk complete: ${chunkShortcuts} shortcuts, ${chunkSkipped} skipped, ${chunkErrors} errors, ${threadsConsumed} threads consumed, offset now ${newOffset}`);

  return {
    status: 'chunk_complete',
    year,
    month,
    offset: newOffset,
    processed: chunkProcessed,
    shortcuts: chunkShortcuts,
    skipped: chunkSkipped,
    errors: chunkErrors,
    moreWork: moreInMonth || month < 12
  };
}

/**
 * Process Drive links chunks for all backfill years.
 * Safe to call from time trigger. Uses BACKFILL_YEARS from Config.gs.
 */
function chunkDriveLinksAll() {
  const results = [];
  for (const year of BACKFILL_YEARS) {
    results.push(processDriveLinksYearChunk(year));
  }
  return results;
}

/**
 * Dry run Drive links backfill for last 30 days.
 */
function dryRunDriveLinks30Days() {
  const date = new Date();
  date.setDate(date.getDate() - 30);
  const sinceDate = Utilities.formatDate(date, 'UTC', 'yyyy/MM/dd');
  return backfillDriveLinks(sinceDate, 50, true);
}

/**
 * View Drive links backfill checkpoints.
 * Uses BACKFILL_YEARS from Config.gs.
 */
function viewDriveLinksCheckpoints() {
  const props = PropertiesService.getScriptProperties();
  const checkpoints = {};
  for (const year of BACKFILL_YEARS) {
    checkpoints[String(year)] = {
      month: props.getProperty(`drivelinks_${year}_month`) || '1',
      offset: props.getProperty(`drivelinks_${year}_offset`) || '0'
    };
  }
  console.log('Drive links backfill checkpoints:', JSON.stringify(checkpoints, null, 2));
  return checkpoints;
}

/**
 * Reset Drive links checkpoint for a year (to restart from month 1).
 * @param {number} year
 */
function resetDriveLinksCheckpoint(year) {
  const props = PropertiesService.getScriptProperties();
  props.deleteProperty(`drivelinks_${year}_month`);
  props.deleteProperty(`drivelinks_${year}_offset`);
  console.log(`Drive links checkpoint for ${year} cleared`);
}

/**
 * Set up trigger for Drive links backfill.
 * Run once from editor to start the sweep.
 * Uses chunkDriveLinksAll which iterates BACKFILL_YEARS from Config.gs.
 */
function setupDriveLinksTriggers() {
  // Clear any existing Drive links triggers
  const triggers = ScriptApp.getProjectTriggers();
  let removed = 0;
  for (const trigger of triggers) {
    const fn = trigger.getHandlerFunction();
    if (fn === 'chunkDriveLinksAll' || fn.startsWith('chunkDriveLinks')) {
      ScriptApp.deleteTrigger(trigger);
      removed++;
    }
  }

  ScriptApp.newTrigger('chunkDriveLinksAll').timeBased().everyMinutes(15).create();

  console.log(`Created 1 Drive links trigger for ${BACKFILL_YEARS.length} years (removed ${removed} existing)`);
  return { created: 1, removed, years: BACKFILL_YEARS, interval: '15 minutes' };
}

/**
 * Clear Drive links triggers (when backfill complete).
 */
function clearDriveLinksTriggers() {
  const triggers = ScriptApp.getProjectTriggers();
  let removed = 0;
  for (const trigger of triggers) {
    const fn = trigger.getHandlerFunction();
    if (fn.startsWith('chunkDriveLinks')) {
      ScriptApp.deleteTrigger(trigger);
      removed++;
    }
  }
  console.log(`Cleared ${removed} Drive links triggers`);
  return { cleared: removed };
}

/**
 * Diagnostic: Test folder scan performance.
 * Run this to see how long Drive-based dedup takes.
 */
function testDedupScan() {
  const monthStr = '2024-07';  // Edit to test different months
  console.log(`Testing dedup scan for ${monthStr}...`);

  const startTime = new Date();
  const { processedIds, contentHashes } = buildDedupStateFromFolder(monthStr);
  const elapsed = (new Date() - startTime) / 1000;

  console.log(`Scan complete in ${elapsed.toFixed(2)}s`);
  console.log(`Found ${processedIds.size} message IDs`);
  console.log(`Found ${contentHashes.size} content hashes`);

  return {
    monthStr,
    elapsedSeconds: elapsed,
    messageIds: processedIds.size,
    contentHashes: contentHashes.size
  };
}

/**
 * Diagnostic: Inspect current in-flight IDs state.
 * Shows what's in Properties storage for each active month.
 */
function testInflightState() {
  // Generate sample months from BACKFILL_YEARS
  const months = [];
  for (const year of BACKFILL_YEARS) {
    months.push(`${year}-01`, `${year}-07`);
  }
  const results = {};

  for (const month of months) {
    const ids = getInflightProcessedIds(month);
    if (ids.size > 0) {
      results[month] = {
        count: ids.size,
        sample: Array.from(ids).slice(0, 5)
      };
    }
  }

  console.log('=== In-Flight IDs State ===');
  console.log(JSON.stringify(results, null, 2));

  // Also show total Properties usage
  const props = PropertiesService.getScriptProperties();
  const all = props.getProperties();
  const inflightKeys = Object.keys(all).filter(k => k.startsWith('inflight_'));
  console.log(`\nTotal inflight_ keys: ${inflightKeys.length}`);
  console.log(`Keys: ${inflightKeys.join(', ')}`);

  return results;
}

/**
 * View current stats by counting files in Drive folders.
 */
function viewStats() {
  const stats = getStats();

  // Count files in Drive folders (source of truth since Drive-based dedup)
  const root = getOrCreateRootFolder();
  const folders = root.getFolders();

  let totalFiles = 0;
  const byYear = {};
  for (const year of BACKFILL_YEARS) {
    byYear[year] = 0;
  }

  while (folders.hasNext()) {
    const folder = folders.next();
    const name = folder.getName();  // e.g., "2024-07"

    // Count files in this folder
    const files = folder.getFiles();
    let count = 0;
    while (files.hasNext()) {
      files.next();
      count++;
    }

    totalFiles += count;

    // Attribute to year
    for (const year of BACKFILL_YEARS) {
      if (name.startsWith(`${year}-`)) {
        byYear[year] += count;
        break;
      }
    }
  }

  console.log('=== Exfiltration Stats (from Drive) ===');
  console.log(`Last run: ${stats.lastRun || 'Never'}`);
  console.log(`Total processed (counter): ${stats.processedCount}`);
  console.log(`Total files in Drive: ${totalFiles}`);
  for (const year of BACKFILL_YEARS) {
    console.log(`${year} files: ${byYear[year]}`);
  }

  return {
    ...stats,
    totalFiles,
    byYear
  };
}

/**
 * Repair file timestamps by reading Date from description and setting createdTime.
 * Processes files in batches to avoid timeouts.
 *
 * @param {number} [batchSize=100] - Files to process per run
 * @param {boolean} [dryRun=false] - If true, report what would be fixed without changing
 * @returns {{processed: number, fixed: number, skipped: number, errors: number}}
 */
function repairFileTimestamps(batchSize = 100, dryRun = false) {
  const root = getOrCreateRootFolder();
  const folders = root.getFolders();

  let processed = 0;
  let fixed = 0;
  let skipped = 0;
  let errors = 0;

  // Get checkpoint to resume from previous run
  const props = PropertiesService.getScriptProperties();
  const lastFolder = props.getProperty('repair_last_folder') || '';
  const lastOffset = parseInt(props.getProperty('repair_last_offset') || '0', 10);
  let currentOffset = 0;
  let reachedCheckpoint = lastFolder === '';

  console.log(`[Repair] Starting${dryRun ? ' [DRY RUN]' : ''}, batch size: ${batchSize}`);
  if (lastFolder) {
    console.log(`[Repair] Resuming from folder ${lastFolder}, offset ${lastOffset}`);
  }

  while (folders.hasNext() && processed < batchSize) {
    const folder = folders.next();
    const folderName = folder.getName();

    // Skip folders until we reach checkpoint
    if (!reachedCheckpoint) {
      if (folderName === lastFolder) {
        reachedCheckpoint = true;
      } else {
        continue;
      }
    }

    const files = folder.getFiles();
    currentOffset = 0;

    while (files.hasNext() && processed < batchSize) {
      const file = files.next();
      currentOffset++;

      // Skip files until we reach offset in checkpoint folder
      if (folderName === lastFolder && currentOffset <= lastOffset) {
        continue;
      }

      try {
        const description = file.getDescription() || '';
        const dateMatch = description.match(/Date:\s*(.+)/);

        if (!dateMatch) {
          skipped++;
          continue;
        }

        const emailDate = new Date(dateMatch[1].trim());
        if (isNaN(emailDate.getTime())) {
          console.log(`[Repair] Invalid date in ${file.getName()}: ${dateMatch[1]}`);
          skipped++;
          continue;
        }

        const fileId = file.getId();
        const currentModified = new Date(file.getLastUpdated());

        // Skip if already correct (within 1 day tolerance)
        const diffDays = Math.abs(currentModified - emailDate) / (1000 * 60 * 60 * 24);
        if (diffDays < 1) {
          skipped++;
          processed++;
          continue;
        }

        if (dryRun) {
          console.log(`[Repair] Would fix: ${file.getName()} (${currentModified.toISOString()} → ${emailDate.toISOString()})`);
        } else {
          // Update modifiedTime via Drive API (createdTime is NOT writable)
          Drive.Files.update(
            { modifiedTime: emailDate.toISOString() },
            fileId
          );
        }

        fixed++;
        processed++;

      } catch (e) {
        console.error(`[Repair] Error on ${file.getName()}: ${e.message}`);
        errors++;
        processed++;
      }
    }

    // Save checkpoint after each folder
    if (!dryRun && processed > 0) {
      props.setProperty('repair_last_folder', folderName);
      props.setProperty('repair_last_offset', String(currentOffset));
    }
  }

  // Clear checkpoint if we finished all folders
  if (!folders.hasNext() && !dryRun) {
    props.deleteProperty('repair_last_folder');
    props.deleteProperty('repair_last_offset');
    console.log('[Repair] Completed all folders, checkpoint cleared');
  }

  const stats = { processed, fixed, skipped, errors, dryRun };
  console.log(`[Repair] Complete: ${JSON.stringify(stats)}`);
  return stats;
}

/**
 * Clear repair checkpoint to start fresh.
 */
function clearRepairCheckpoint() {
  const props = PropertiesService.getScriptProperties();
  props.deleteProperty('repair_last_folder');
  props.deleteProperty('repair_last_offset');
  console.log('[Repair] Checkpoint cleared');
  return { cleared: true };
}

/**
 * Dry run repair - preview what would be fixed without changing anything.
 * Processes 200 files to get a representative sample.
 */
function dryRunRepair() {
  return repairFileTimestamps(200, true);
}

/**
 * View backfill checkpoints and offsets.
 * Uses BACKFILL_YEARS from Config.gs.
 */
function viewCheckpoints() {
  const props = PropertiesService.getScriptProperties();
  const checkpoints = {};
  for (const year of BACKFILL_YEARS) {
    checkpoints[String(year)] = {
      month: props.getProperty(`backfill_${year}_month`) || '1',
      offset: props.getProperty(`backfill_${year}_offset`) || '0'
    };
  }
  console.log('Backfill checkpoints:', JSON.stringify(checkpoints, null, 2));
  return checkpoints;
}

/**
 * Reset checkpoint for a year (to restart from month 1).
 * @param {number} year
 */
function resetCheckpoint(year) {
  const props = PropertiesService.getScriptProperties();
  props.deleteProperty(`backfill_${year}_month`);
  props.deleteProperty(`backfill_${year}_offset`);
  console.log(`Checkpoint for ${year} cleared (month and offset)`);
}

/**
 * Clear legacy Properties-based dedup data.
 * Now that we use Drive-based dedup, these are no longer needed
 * and were causing quota errors.
 */
function clearLegacyDedupProperties() {
  const props = PropertiesService.getScriptProperties();
  const keysToDelete = [
    PROCESSED_IDS_KEY,           // processedMessageIds
    CONTENT_HASHES_KEY,          // contentHashes
    ...BACKFILL_YEARS.map(y => `processedIds_${y}`),
  ];

  let deleted = 0;
  for (const key of keysToDelete) {
    const existed = props.getProperty(key) !== null;
    props.deleteProperty(key);
    if (existed) {
      console.log(`Deleted: ${key}`);
      deleted++;
    }
  }

  console.log(`Cleared ${deleted} legacy dedup properties`);
  return { deleted, keys: keysToDelete };
}

/**
 * Reset all state (use with caution).
 */
function resetState() {
  const props = PropertiesService.getScriptProperties();
  props.deleteAllProperties();
  console.log('State reset complete');
}

// ============================================================================
// TRIGGER MANAGEMENT
// ============================================================================

/**
 * Set up time-driven trigger for backfill.
 * Uses chunkBackfillAll which iterates BACKFILL_YEARS from Config.gs.
 * Run once via: itv-appscript run setupTriggers --dev
 */
function setupTriggers() {
  // Clear existing triggers first to avoid duplicates
  clearTriggers();

  ScriptApp.newTrigger('chunkBackfillAll').timeBased().everyMinutes(15).create();

  console.log(`Created 1 trigger for ${BACKFILL_YEARS.length} years - every 15 minutes`);
  return { created: 1, years: BACKFILL_YEARS, interval: '15 minutes' };
}

/**
 * Set up a trigger to repair file timestamps in batches.
 * Runs every 5 minutes until all files are processed, then auto-removes itself.
 */
function setupRepairTrigger() {
  // Remove any existing repair trigger
  const triggers = ScriptApp.getProjectTriggers();
  for (const trigger of triggers) {
    if (trigger.getHandlerFunction() === 'triggeredRepair') {
      ScriptApp.deleteTrigger(trigger);
    }
  }

  ScriptApp.newTrigger('triggeredRepair').timeBased().everyMinutes(5).create();
  console.log('Created repair trigger (every 5 minutes)');
  return { created: true, interval: '5 minutes' };
}

/**
 * Triggered repair function - processes batch and auto-removes trigger when done.
 */
function triggeredRepair() {
  const result = repairFileTimestamps(500, false);
  console.log(`[Repair Trigger] ${JSON.stringify(result)}`);

  // If no files processed, we're done - remove the trigger
  if (result.processed === 0 || (result.fixed === 0 && result.errors === 0)) {
    const triggers = ScriptApp.getProjectTriggers();
    for (const trigger of triggers) {
      if (trigger.getHandlerFunction() === 'triggeredRepair') {
        ScriptApp.deleteTrigger(trigger);
        console.log('[Repair Trigger] Complete - trigger removed');
      }
    }
  }

  return result;
}

/**
 * Clear all triggers for this project.
 */
function clearTriggers() {
  const triggers = ScriptApp.getProjectTriggers();
  for (const trigger of triggers) {
    ScriptApp.deleteTrigger(trigger);
  }
  console.log(`Cleared ${triggers.length} triggers`);
  return { cleared: triggers.length };
}

/**
 * List current triggers.
 */
function listTriggers() {
  const triggers = ScriptApp.getProjectTriggers();
  const info = triggers.map(t => ({
    function: t.getHandlerFunction(),
    type: t.getEventType().toString(),
  }));
  console.log('Current triggers:', JSON.stringify(info, null, 2));
  return info;
}

/**
 * Set up ongoing trigger for new emails (post-backfill).
 * Runs daily to process emails since last run.
 *
 * Call this once backfill is complete:
 *   itv-appscript run setupOngoingTrigger --dev
 *
 * Or from Apps Script editor: Run > setupOngoingTrigger
 */
function setupOngoingTrigger() {
  // Remove any existing processNewEmails trigger to avoid duplicates
  const triggers = ScriptApp.getProjectTriggers();
  let removed = 0;
  for (const trigger of triggers) {
    if (trigger.getHandlerFunction() === 'processNewEmails') {
      ScriptApp.deleteTrigger(trigger);
      removed++;
    }
  }

  // Create 15-minute trigger for near-real-time processing
  ScriptApp.newTrigger('processNewEmails')
    .timeBased()
    .everyMinutes(15)
    .create();

  console.log(`Created 15-minute trigger for processNewEmails (removed ${removed} existing)`);
  return { created: true, interval: 'every 15 minutes', removed };
}

/**
 * Remove the ongoing trigger (if you need to pause processing).
 */
function clearOngoingTrigger() {
  const triggers = ScriptApp.getProjectTriggers();
  let removed = 0;
  for (const trigger of triggers) {
    if (trigger.getHandlerFunction() === 'processNewEmails') {
      ScriptApp.deleteTrigger(trigger);
      removed++;
    }
  }
  console.log(`Cleared ${removed} processNewEmails triggers`);
  return { cleared: removed };
}
