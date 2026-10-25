//! Version comparison for update checks: dotted numbers with an optional
//! SemVer pre-release (`1.2.0-beta.2`) and build metadata (`+abc`, ignored).
//! A leading `v` is allowed. Missing trailing parts count as 0 (1.0 == 1.0.0).

use std::cmp::Ordering;

#[derive(Debug, Clone, PartialEq, Eq)]
enum Ident {
    Num(u64),
    Text(String),
}

impl Ord for Ident {
    fn cmp(&self, other: &Self) -> Ordering {
        match (self, other) {
            (Ident::Num(a), Ident::Num(b)) => a.cmp(b),
            (Ident::Num(_), Ident::Text(_)) => Ordering::Less,
            (Ident::Text(_), Ident::Num(_)) => Ordering::Greater,
            (Ident::Text(a), Ident::Text(b)) => a.cmp(b),
        }
    }
}

impl PartialOrd for Ident {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Version {
    core: Vec<u64>,
    pre: Vec<Ident>,
}

fn leading_number(s: &str) -> u64 {
    let digits: String = s.chars().take_while(|c| c.is_ascii_digit()).collect();
    if digits.is_empty() {
        0
    } else {
        digits.parse().unwrap_or(u64::MAX)
    }
}

/// None if the string does not start with a digit (after an optional `v`).
pub fn parse(s: &str) -> Option<Version> {
    let s = s.trim();
    let s = s.strip_prefix(['v', 'V']).unwrap_or(s);
    if !s.starts_with(|c: char| c.is_ascii_digit()) {
        return None;
    }
    let s = s.split('+').next().unwrap_or(s);
    let (core, pre) = match s.split_once('-') {
        Some((c, p)) => (c, Some(p)),
        None => (s, None),
    };
    let core = core.split('.').map(leading_number).collect();
    let pre = pre
        .map(|p| {
            p.split('.')
                .map(|id| {
                    if !id.is_empty() && id.chars().all(|c| c.is_ascii_digit()) {
                        Ident::Num(id.parse().unwrap_or(u64::MAX))
                    } else {
                        Ident::Text(id.to_ascii_lowercase())
                    }
                })
                .collect()
        })
        .unwrap_or_default();
    Some(Version { core, pre })
}

impl Ord for Version {
    fn cmp(&self, other: &Self) -> Ordering {
        let n = self.core.len().max(other.core.len());
        for i in 0..n {
            let a = self.core.get(i).copied().unwrap_or(0);
            let b = other.core.get(i).copied().unwrap_or(0);
            match a.cmp(&b) {
                Ordering::Equal => {}
                o => return o,
            }
        }
        match (self.pre.is_empty(), other.pre.is_empty()) {
            (true, true) => Ordering::Equal,
            (true, false) => Ordering::Greater, // 1.0.0 > 1.0.0-rc.1
            (false, true) => Ordering::Less,
            (false, false) => self.pre.cmp(&other.pre),
        }
    }
}

impl PartialOrd for Version {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

/// Unparsable versions sort below every real version.
pub fn compare(a: &str, b: &str) -> Ordering {
    match (parse(a), parse(b)) {
        (Some(x), Some(y)) => x.cmp(&y),
        (Some(_), None) => Ordering::Greater,
        (None, Some(_)) => Ordering::Less,
        (None, None) => Ordering::Equal,
    }
}

pub fn is_newer(candidate: &str, current: &str) -> bool {
    compare(candidate, current) == Ordering::Greater
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn numeric_parts() {
        assert!(is_newer("0.2.0", "0.1.0"));
        assert!(is_newer("0.10.0", "0.9.9"));
        assert!(is_newer("1.0.1", "1.0"));
        assert!(!is_newer("0.1.0", "0.1.0"));
        assert_eq!(compare("1.0", "1.0.0"), Ordering::Equal);
        assert_eq!(compare("v1.2.3", "1.2.3"), Ordering::Equal);
        assert_eq!(compare("1.2.3+build.7", "1.2.3"), Ordering::Equal);
    }

    #[test]
    fn semver_precedence() {
        // The ordering example from the SemVer spec.
        let chain = [
            "1.0.0-alpha",
            "1.0.0-alpha.1",
            "1.0.0-alpha.beta",
            "1.0.0-beta",
            "1.0.0-beta.2",
            "1.0.0-beta.11",
            "1.0.0-rc.1",
            "1.0.0",
        ];
        for w in chain.windows(2) {
            assert_eq!(compare(w[0], w[1]), Ordering::Less, "{} < {}", w[0], w[1]);
            assert!(is_newer(w[1], w[0]));
        }
    }

    #[test]
    fn garbage_is_lowest() {
        assert!(is_newer("0.0.1", "abc"));
        assert!(!is_newer("", "0.0.1"));
        assert_eq!(compare("x", "y"), Ordering::Equal);
        assert!(is_newer("2", "1.99.99"));
    }
}
