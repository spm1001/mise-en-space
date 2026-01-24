# Heading 1

## Heading 2

### Heading 3

#### Heading 4

##### Heading 5

###### Heading 6

---

## Text Formatting

This is **bold text** and this is *italic text*.

This is ***bold italic*** and this is ~~strikethrough~~.

This is `inline code` in a sentence.

## Links

Here is a [link to Google](https://google.com).

Here is a bare URL: https://example.com

## Lists

### Unordered
- Item one
- Item two
  - Nested item
  - Another nested
    - Deep nested
- Item three

### Ordered
1. First
2. Second
   1. Nested numbered
   2. Another
3. Third

### Task List
- [ ] Unchecked task
- [x] Checked task
- [ ] Another unchecked

## Blockquote

> This is a blockquote.
> It can span multiple lines.
>
> And have paragraphs.

## Code Blocks

```python
def hello():
    print("Hello, world!")
```

```javascript
const x = 42;
console.log(x);
```

## Tables

| Column A | Column B | Column C |
|----------|----------|----------|
| Row 1A   | Row 1B   | Row 1C   |
| Row 2A   | Row 2B   | Row 2C   |
| Row 3A   | Row 3B   | Row 3C   |

## Images

This tests an image reference (placeholder URL):

![Alt text for image](https://via.placeholder.com/150)

## Horizontal Rules

Above the rule

---

Below the rule

---

Another rule above

***

Rule with asterisks

## Special Characters

These should survive the roundtrip:

- Ampersand: &
- Less than: <
- Greater than: >
- Pipe: |
- Backslash: \
- Backtick: `
- Quotes: "double" and 'single'
- Curly quotes: "smart" and 'apostrophe'

## Unicode

- Emoji: 🎉 🚀 ✅ 🔥 💡
- CJK: 你好世界 (Chinese)
- Japanese: こんにちは
- Korean: 안녕하세요
- Greek: Ωμέγα αβγ
- Cyrillic: Привет
- Arabic: مرحبا
- Hebrew: שלום
- Math symbols: ∑ ∫ √ ∞ ≠ ≤ ≥
- Arrows: → ← ↑ ↓ ⇒ ⇐

## Nested Formatting

- **Bold item** with *italic inside*
- A list item with `code` and [a link](https://example.com)
- ***Bold italic item***

## Long Paragraph

Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.

## Multiple Paragraphs

First paragraph here.

Second paragraph here.

Third paragraph here.

## Edge Cases

### Empty list items handling
-
- Non-empty item
-

### Indented code block (4 spaces)

    This is indented code
    It spans multiple lines
    And should be preserved

### HTML entities (if preserved)

&copy; &reg; &trade; &mdash; &ndash; &hellip;

### Escape sequences

\*not italic\*

\*\*not bold\*\*

\[not a link\](url)

\`not code\`

---

## End of Test Document

If this line appears, the full document was processed.
