// One place that maps an entry to a label and a colour tone, shared by the
// sidebar dots and the chat thread.
export function statusOf(entry) {
  if (entry.pending) return { tone: "live", label: "Working" };
  if (entry.error) return { tone: "warning", label: "Couldn't reach the assistant" };
  switch (entry.response?.status) {
    case "ok":
      return { tone: "ok", label: "Recommendation" };
    case "partial":
      return { tone: "pending", label: "Recommendation with caveats" };
    case "needs_clarification":
      return { tone: "info", label: "Needs a place" };
    case "out_of_scope":
      return { tone: "muted", label: "Not an outdoor question" };
    case "busy":
      return { tone: "warning", label: "Assistant was busy" };
    default:
      return { tone: "muted", label: "Unknown reply" };
  }
}
