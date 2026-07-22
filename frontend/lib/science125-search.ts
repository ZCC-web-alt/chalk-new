const STOP_WORDS = new Set([
  "about", "after", "again", "against", "among", "because", "before", "being", "between",
  "could", "does", "from", "have", "into", "itself", "level", "might", "other", "should",
  "their", "there", "these", "those", "through", "under", "using", "what", "when", "where",
  "which", "while", "with", "would", "your", "ours", "they", "them", "this", "that", "than",
  "then", "will", "ever", "make", "made", "much", "many", "more", "most", "some", "such",
  "only", "also", "very", "still", "were", "been", "being", "how", "can", "we", "the", "and",
  "for", "are", "but", "not", "you", "its", "our", "has", "had", "was", "why", "who",
])

function tokens(value: string) {
  return value
    .normalize("NFC")
    .toLocaleLowerCase()
    .match(/[a-z][a-z0-9]*(?:[-–][a-z0-9]+)*/g) || []
}

export function buildScience125SearchQuery(question: string, sourceContext: string) {
  const ordered = [...tokens(question), ...tokens(sourceContext)]
  const selected: string[] = []
  const seen = new Set<string>()
  for (const token of ordered) {
    if (token.length < 4 || STOP_WORDS.has(token) || seen.has(token)) continue
    seen.add(token)
    selected.push(token)
    if (selected.length >= 16) break
  }
  return selected.length >= 4 ? selected.join(" ") : question.trim()
}
