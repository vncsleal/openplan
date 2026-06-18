const STOP_WORDS = new Set([
  "the",
  "a",
  "an",
  "and",
  "or",
  "but",
  "in",
  "on",
  "at",
  "to",
  "for",
  "of",
  "with",
  "by",
  "from",
  "as",
  "is",
  "was",
  "are",
  "were",
  "be",
  "been",
  "being",
  "have",
  "has",
  "had",
  "do",
  "does",
  "did",
  "will",
  "would",
  "could",
  "should",
  "may",
  "might",
  "shall",
  "can",
  "need",
  "dare",
  "ought",
  "used",
  "this",
  "that",
  "these",
  "those",
  "it",
  "its",
  "they",
  "them",
  "their",
  "we",
  "us",
  "our",
  "you",
  "your",
  "he",
  "him",
  "his",
  "she",
  "her",
  "hers",
]);

const PUNCTUATION_RE = /[^\w\s]/g;
const WHITESPACE_RE = /\s+/g;

export function tokenize(input: string): string {
  return input
    .toLowerCase()
    .replace(PUNCTUATION_RE, " ")
    .replace(WHITESPACE_RE, " ")
    .trim()
    .split(/\s+/)
    .filter((t) => t.length > 0 && !STOP_WORDS.has(t))
    .slice(0, 50)
    .join(" ");
}

export function matchLevel(
  goalTokens: string,
  labelTokens: string,
  action: string,
): "exact" | "label_keyword" | "action" {
  const goalSet = new Set(goalTokens.split(/\s+/));
  const labelSet = new Set(labelTokens.split(/\s+/));
  const overlap = [...goalSet].filter((t) => labelSet.has(t)).length;

  if (overlap >= 2) return "exact";

  if (labelSet.size >= 1 && labelTokens.length > 0) return "label_keyword";

  return "action";
}
