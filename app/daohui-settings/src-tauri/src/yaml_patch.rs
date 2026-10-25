//! A small line editor for Rime `*.custom.yaml` patch files.
//!
//! Only flat keys directly under the top-level `patch:` map are read or
//! written (`asus_lang/raw_keys: true`, `"switches/@last/reset": 0`).
//! Everything else -- comments, blank lines, unknown keys, nested values,
//! other top-level keys, line endings and a UTF-8 BOM -- is kept as it was.
//! The file is never re-serialized as a whole.

#[derive(Debug, Clone)]
pub struct PatchFile {
    bom: bool,
    eol: &'static str,
    lines: Vec<String>,
    final_newline: bool,
}

/// A `key: value` line.
#[derive(Debug)]
struct KeyLine<'a> {
    key: String,
    /// Byte index of the `:` that ends the key.
    colon: usize,
    /// The value without comment and surrounding spaces ("" if none).
    value: &'a str,
    /// The trailing comment including the spaces before `#` ("" if none).
    comment: &'a str,
}

/// The `patch:` block: lines `header + 1 .. end`.
#[derive(Debug)]
struct Block {
    header: usize,
    end: usize,
    /// `patch: {}` written in flow style (empty).
    flow_empty: bool,
    child_indent: Option<usize>,
}

/// An entry of the patch map, lines `line .. end` (nested lines included).
#[derive(Debug)]
struct Entry {
    line: usize,
    end: usize,
    key: String,
}

fn indent_of(line: &str) -> usize {
    line.len() - line.trim_start_matches(' ').len()
}

/// Blank or comment-only line.
fn is_meta(line: &str) -> bool {
    let t = line.trim();
    t.is_empty() || t.starts_with('#')
}

/// Parses a quoted scalar at the start of `s`; returns the text and the
/// number of bytes consumed (quotes included).
fn parse_quoted(s: &str) -> Option<(String, usize)> {
    let mut it = s.char_indices();
    let (_, q) = it.next()?;
    let mut out = String::new();
    match q {
        '"' => {
            while let Some((i, c)) = it.next() {
                match c {
                    '"' => return Some((out, i + 1)),
                    '\\' => {
                        let (_, e) = it.next()?;
                        out.push(match e {
                            'n' => '\n',
                            't' => '\t',
                            '0' => '\0',
                            other => other,
                        });
                    }
                    c => out.push(c),
                }
            }
            None
        }
        '\'' => {
            let mut it = it.peekable();
            while let Some((i, c)) = it.next() {
                if c == '\'' {
                    if matches!(it.peek(), Some((_, '\''))) {
                        it.next();
                        out.push('\'');
                    } else {
                        return Some((out, i + 1));
                    }
                } else {
                    out.push(c);
                }
            }
            None
        }
        _ => None,
    }
}

/// Position of the `:` ending a plain (unquoted) key.
fn plain_colon(s: &str) -> Option<usize> {
    let b = s.as_bytes();
    for i in 0..b.len() {
        if b[i] == b'#' && i > 0 && (b[i - 1] == b' ' || b[i - 1] == b'\t') {
            return None;
        }
        if b[i] == b':' && (i + 1 == b.len() || b[i + 1] == b' ' || b[i + 1] == b'\t') {
            return Some(i);
        }
    }
    None
}

/// Splits what follows the colon into (value, comment).
fn split_comment(s: &str) -> (&str, &str) {
    let b = s.as_bytes();
    let mut i = s.len() - s.trim_start().len();
    if i < b.len() && (b[i] == b'"' || b[i] == b'\'') {
        match parse_quoted(&s[i..]) {
            Some((_, n)) => i += n,
            None => i = b.len(),
        }
    }
    while i < b.len() {
        if b[i] == b'#' && (i == 0 || b[i - 1] == b' ' || b[i - 1] == b'\t') {
            let mut c = i;
            while c > 0 && (b[c - 1] == b' ' || b[c - 1] == b'\t') {
                c -= 1;
            }
            return (s[..c].trim(), &s[c..]);
        }
        i += 1;
    }
    (s.trim(), "")
}

fn parse_key_line(line: &str) -> Option<KeyLine<'_>> {
    let indent = indent_of(line);
    let rest = &line[indent..];
    if rest.is_empty()
        || rest.starts_with('#')
        || rest.starts_with('-')
        || rest.starts_with('[')
        || rest.starts_with('{')
    {
        return None;
    }
    let (key, colon) = if rest.starts_with('"') || rest.starts_with('\'') {
        let (k, n) = parse_quoted(rest)?;
        let after = rest[n..].trim_start_matches([' ', '\t']);
        if !after.starts_with(':') {
            return None;
        }
        (k, indent + rest.len() - after.len())
    } else {
        let c = plain_colon(rest)?;
        (rest[..c].trim_end().to_string(), indent + c)
    };
    let after = &line[colon + 1..];
    if !(after.is_empty() || after.starts_with(' ') || after.starts_with('\t')) {
        return None;
    }
    let (value, comment) = split_comment(after);
    Some(KeyLine { key, colon, value, comment })
}

fn unquote(value: &str) -> String {
    if value.starts_with('"') || value.starts_with('\'') {
        if let Some((s, _)) = parse_quoted(value) {
            return s;
        }
    }
    value.to_string()
}

/// Writes a key plainly when that is unambiguous, otherwise double-quoted.
pub fn format_key(key: &str) -> String {
    let plain = !key.is_empty()
        && key.chars().enumerate().all(|(i, c)| {
            c.is_ascii_alphanumeric() || c == '_' || (i > 0 && matches!(c, '/' | '-' | '.'))
        });
    if plain {
        key.to_string()
    } else {
        format!("\"{}\"", key.replace('\\', "\\\\").replace('"', "\\\""))
    }
}

impl PatchFile {
    pub fn parse(text: &str) -> Self {
        let (bom, body) = match text.strip_prefix('\u{feff}') {
            Some(b) => (true, b),
            None => (false, text),
        };
        let eol = if body.contains("\r\n") { "\r\n" } else { "\n" };
        let mut lines: Vec<String> = body
            .split('\n')
            .map(|l| l.strip_suffix('\r').unwrap_or(l).to_string())
            .collect();
        let final_newline = body.is_empty() || body.ends_with('\n');
        if body.ends_with('\n') || body.is_empty() {
            lines.pop();
        }
        PatchFile { bom, eol, lines, final_newline }
    }

    pub fn render(&self) -> String {
        let mut s = String::new();
        if self.bom {
            s.push('\u{feff}');
        }
        s.push_str(&self.lines.join(self.eol));
        if self.final_newline && !self.lines.is_empty() {
            s.push_str(self.eol);
        }
        s
    }

    fn block(&self) -> Result<Option<Block>, String> {
        let Some(header) = self.lines.iter().position(|l| {
            indent_of(l) == 0 && parse_key_line(l).is_some_and(|k| k.key == "patch")
        }) else {
            return Ok(None);
        };
        let value = parse_key_line(&self.lines[header]).map(|k| k.value).unwrap_or("");
        let flow_empty = value == "{}";
        if !value.is_empty() && !flow_empty {
            return Err("patch: 寫成了單行格式，無法自動修改；請手動改成每行一個設定。".into());
        }
        let end = (header + 1..self.lines.len())
            .find(|&j| !is_meta(&self.lines[j]) && indent_of(&self.lines[j]) == 0)
            .unwrap_or(self.lines.len());
        if flow_empty {
            // Anything indented under `patch: {}` would be invalid YAML anyway.
            return Ok(Some(Block { header, end: header + 1, flow_empty, child_indent: None }));
        }
        let child_indent = (header + 1..end)
            .find(|&j| !is_meta(&self.lines[j]))
            .map(|j| indent_of(&self.lines[j]));
        Ok(Some(Block { header, end, flow_empty, child_indent }))
    }

    fn entries(&self, block: &Block) -> Vec<Entry> {
        let Some(ci) = block.child_indent else {
            return Vec::new();
        };
        let mut out = Vec::new();
        let mut j = block.header + 1;
        while j < block.end {
            let line = &self.lines[j];
            if !is_meta(line) && indent_of(line) == ci {
                if let Some(kl) = parse_key_line(line) {
                    // Nested lines: deeper-indented content up to the next entry.
                    let mut end = j + 1;
                    let mut k = j + 1;
                    while k < block.end {
                        let l = &self.lines[k];
                        if !is_meta(l) {
                            if indent_of(l) <= ci {
                                break;
                            }
                            end = k + 1;
                        }
                        k += 1;
                    }
                    out.push(Entry { line: j, end, key: kl.key });
                    j = end;
                    continue;
                }
            }
            j += 1;
        }
        out
    }

    /// Fails if the file has a `patch:` this editor cannot change.
    pub fn check(&self) -> Result<(), String> {
        self.block().map(|_| ())
    }

    pub fn contains(&self, key: &str) -> bool {
        match self.block() {
            Ok(Some(b)) => self.entries(&b).iter().any(|e| e.key == key),
            _ => false,
        }
    }

    /// The scalar value of `key` (unquoted), or None if absent or not a scalar.
    pub fn get(&self, key: &str) -> Option<String> {
        let block = self.block().ok()??;
        let entry = self.entries(&block).into_iter().rfind(|e| e.key == key)?;
        let kl = parse_key_line(&self.lines[entry.line])?;
        if kl.value.is_empty()
            || kl.value.starts_with('|')
            || kl.value.starts_with('>')
            || kl.value.starts_with('&')
            || kl.value.starts_with('*')
            || kl.value.starts_with('{')
            || kl.value.starts_with('[')
        {
            return None;
        }
        Some(unquote(kl.value))
    }

    pub fn get_bool(&self, key: &str) -> Option<bool> {
        match self.get(key)?.to_ascii_lowercase().as_str() {
            "true" | "yes" | "on" | "y" => Some(true),
            "false" | "no" | "off" | "n" => Some(false),
            _ => None,
        }
    }

    pub fn get_int(&self, key: &str) -> Option<i64> {
        let v = self.get(key)?;
        v.strip_prefix('+').unwrap_or(&v).parse().ok()
    }

    /// Sets `key` to `value` (already YAML text, e.g. `true`, `400`). An
    /// existing line keeps its key spelling and trailing comment; a new key
    /// goes after the last entry of the patch map, which is created if needed.
    pub fn set(&mut self, key: &str, value: &str) -> Result<(), String> {
        match self.block()? {
            None => {
                if self.lines.last().is_some_and(|l| !l.trim().is_empty()) {
                    self.lines.push(String::new());
                }
                self.lines.push("patch:".into());
                self.lines.push(format!("  {}: {}", format_key(key), value));
                self.final_newline = true;
            }
            Some(block) if block.flow_empty => {
                let comment = parse_key_line(&self.lines[block.header])
                    .map(|k| k.comment.to_string())
                    .unwrap_or_default();
                self.lines[block.header] = format!("patch:{comment}");
                self.lines.insert(block.header + 1, format!("  {}: {}", format_key(key), value));
            }
            Some(block) => {
                let entries = self.entries(&block);
                let matching: Vec<&Entry> = entries.iter().filter(|e| e.key == key).collect();
                if matching.is_empty() {
                    let indent = " ".repeat(block.child_indent.unwrap_or(2));
                    let at = (block.header + 1..block.end)
                        .rev()
                        .find(|&j| !is_meta(&self.lines[j]))
                        .map(|j| j + 1)
                        .unwrap_or(block.header + 1);
                    self.lines.insert(at, format!("{indent}{}: {}", format_key(key), value));
                } else {
                    for e in matching.iter().rev() {
                        let new_line = {
                            let line = &self.lines[e.line];
                            let kl = parse_key_line(line).expect("entry line parses");
                            format!("{}: {}{}", &line[..kl.colon], value, kl.comment)
                        };
                        self.lines.splice(e.line..e.end, [new_line]);
                    }
                }
            }
        }
        Ok(())
    }

    /// Removes `key` (with any nested lines). Returns whether it was there.
    #[allow(dead_code)]
    pub fn remove(&mut self, key: &str) -> bool {
        let Ok(Some(block)) = self.block() else {
            return false;
        };
        let entries = self.entries(&block);
        let matching: Vec<&Entry> = entries.iter().filter(|e| e.key == key).collect();
        for e in matching.iter().rev() {
            self.lines.drain(e.line..e.end);
        }
        !matching.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE: &str = r#"# ASUS-style Zhuyin schema customization
# encoding: utf-8

patch:
  # "Raw keys first" inline preedit mode
  # Default: false.
  asus_lang/raw_keys: true
"#;

    #[test]
    fn reads_the_repo_sample() {
        let f = PatchFile::parse(SAMPLE);
        assert_eq!(f.get_bool("asus_lang/raw_keys"), Some(true));
        assert_eq!(f.get("asus_tab/double_ms"), None);
        assert!(f.contains("asus_lang/raw_keys"));
        assert_eq!(f.render(), SAMPLE);
    }

    #[test]
    fn set_existing_keeps_everything_else() {
        let mut f = PatchFile::parse(SAMPLE);
        f.set("asus_lang/raw_keys", "false").unwrap();
        assert_eq!(f.render(), SAMPLE.replace("raw_keys: true", "raw_keys: false"));
    }

    #[test]
    fn insert_after_last_entry_with_detected_indent() {
        let src = "patch:\n    a/b: 1   # one\n    c/d:\n      - x\n      - y\n\n# trailing note\nother: 1\n";
        let mut f = PatchFile::parse(src);
        f.set("asus_tab/double_ms", "500").unwrap();
        assert_eq!(
            f.render(),
            "patch:\n    a/b: 1   # one\n    c/d:\n      - x\n      - y\n    asus_tab/double_ms: 500\n\n# trailing note\nother: 1\n"
        );
        // Trailing comment survives a change of value.
        f.set("a/b", "2").unwrap();
        assert!(f.render().contains("    a/b: 2   # one\n"));
        assert_eq!(f.get_int("a/b"), Some(2));
        // Nested value is not a scalar, but can be replaced by one.
        assert_eq!(f.get("c/d"), None);
        f.set("c/d", "3").unwrap();
        assert!(f.render().contains("    c/d: 3\n    asus_tab/double_ms: 500\n"));
        assert!(!f.render().contains("- x"));
    }

    #[test]
    fn creates_patch_section_when_missing() {
        let mut f = PatchFile::parse("# only a comment\nfoo: bar");
        f.set("asus_learn/enabled", "false").unwrap();
        assert_eq!(f.render(), "# only a comment\nfoo: bar\n\npatch:\n  asus_learn/enabled: false\n");

        let mut empty = PatchFile::parse("");
        empty.set("x", "1").unwrap();
        assert_eq!(empty.render(), "patch:\n  x: 1\n");
    }

    #[test]
    fn quoted_keys_and_special_keys() {
        let src = "patch:\n  \"switches/@last/reset\": 0  # prediction off\n  'asus_tab/double_ms': \"450\"\n";
        let mut f = PatchFile::parse(src);
        assert_eq!(f.get_int("switches/@last/reset"), Some(0));
        assert_eq!(f.get_int("asus_tab/double_ms"), Some(450));
        f.set("switches/@last/reset", "1").unwrap();
        assert!(f.render().contains("  \"switches/@last/reset\": 1  # prediction off\n"));
        // A new special key is quoted.
        let mut g = PatchFile::parse("patch:\n  a: 1\n");
        g.set("switches/@last/reset", "0").unwrap();
        assert_eq!(g.render(), "patch:\n  a: 1\n  \"switches/@last/reset\": 0\n");
        assert_eq!(format_key("punctuator/half_shape/:"), "\"punctuator/half_shape/:\"");
        assert_eq!(format_key("asus_lang/raw_keys"), "asus_lang/raw_keys");
    }

    #[test]
    fn hash_inside_quotes_is_not_a_comment() {
        let f = PatchFile::parse("patch:\n  k: \"a # b\" # real\n  \"punctuator/half_shape/#\": { commit: \"#\" }\n");
        assert_eq!(f.get("k").as_deref(), Some("a # b"));
        assert!(f.contains("punctuator/half_shape/#"));
        assert_eq!(f.get("punctuator/half_shape/#"), None); // flow map, not a scalar
    }

    #[test]
    fn crlf_and_bom_are_preserved() {
        let src = "\u{feff}patch:\r\n  asus_learn/enabled: true\r\n";
        let mut f = PatchFile::parse(src);
        f.set("asus_learn/enabled", "false").unwrap();
        f.set("asus_tab/double_ms", "600").unwrap();
        assert_eq!(
            f.render(),
            "\u{feff}patch:\r\n  asus_learn/enabled: false\r\n  asus_tab/double_ms: 600\r\n"
        );
    }

    #[test]
    fn flow_patch() {
        let mut f = PatchFile::parse("patch: {}  # nothing yet\n");
        f.set("a/b", "true").unwrap();
        assert_eq!(f.render(), "patch:  # nothing yet\n  a/b: true\n");

        let mut g = PatchFile::parse("patch: { a: 1 }\n");
        assert!(g.check().is_err());
        assert!(g.set("a", "2").is_err());
        assert_eq!(g.get("a"), None);
    }

    #[test]
    fn remove_and_duplicates() {
        let mut f = PatchFile::parse("patch:\n  a: 1\n  b:\n    c: 2\n  a: 3\n  d: 4\n");
        assert_eq!(f.get_int("a"), Some(3)); // last one wins when reading
        f.set("a", "5").unwrap(); // all copies are updated
        assert_eq!(f.render(), "patch:\n  a: 5\n  b:\n    c: 2\n  a: 5\n  d: 4\n");
        assert!(f.remove("b"));
        assert!(!f.remove("zzz"));
        assert_eq!(f.render(), "patch:\n  a: 5\n  a: 5\n  d: 4\n");
    }

    #[test]
    fn column_zero_comments_stay_inside_block() {
        let src = "patch:\n  a: 1\n# a comment at column 0\n  b: 2\n";
        let mut f = PatchFile::parse(src);
        assert_eq!(f.get_int("b"), Some(2));
        f.set("c", "3").unwrap();
        assert_eq!(f.render(), "patch:\n  a: 1\n# a comment at column 0\n  b: 2\n  c: 3\n");
    }

    #[test]
    fn no_final_newline_is_kept() {
        let mut f = PatchFile::parse("patch:\n  a: 1");
        f.set("a", "2").unwrap();
        assert_eq!(f.render(), "patch:\n  a: 2");
    }

    #[test]
    fn bools() {
        let f = PatchFile::parse("patch:\n  a: yes\n  b: Off\n  c: maybe\n");
        assert_eq!(f.get_bool("a"), Some(true));
        assert_eq!(f.get_bool("b"), Some(false));
        assert_eq!(f.get_bool("c"), None);
    }
}
