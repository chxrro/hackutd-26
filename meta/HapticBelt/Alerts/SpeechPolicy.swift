/// Speaks the current assessment only; never queues old warnings.
struct SpeechPolicy {
  struct Output {
    let phrase: String?
    let cancel: Bool
  }
  private var currentKey: String?
  private var spokenKey: String?
  private var nextSpeech = 0.0
  private var nextReminder = 0.0
  private var urgency: AlertUrgency = .notice
  private var lastEscalation = -Double.infinity
  private var repeatRequested = false

  mutating func requestRepeat() { repeatRequested = true }

  mutating func step(_ assessment: AlertAssessment, enabled: Bool, active: Bool,
                     detailed: Bool, now: Double, reminders: Bool = false) -> Output {
    guard enabled, active else {
      let cancel = currentKey != nil
      currentKey = nil
      spokenKey = nil
      nextSpeech = now
      nextReminder = now
      urgency = .notice
      repeatRequested = false
      return Output(phrase: nil, cancel: cancel)
    }
    let previousUrgency = urgency
    urgency = assessment.kind == .obstacle
      ? AlertUrgency.resolve(level: assessment.level, previous: urgency) : .notice
    let key = assessment.key + (urgency == .urgent ? ":urgent" : "")
    let changed = currentKey != key
    currentKey = key
    if assessment.kind == .quiet {
      spokenKey = nil
      let requested = repeatRequested
      repeatRequested = false
      // Only on explicit request; silence is never announced as a clear path.
      return Output(phrase: requested ? "No current alert. This does not mean the path is clear." : nil,
                    cancel: changed || requested)
    }
    let escalation = urgency == .urgent && previousUrgency != .urgent && now - lastEscalation >= 2
    let manual = repeatRequested
    repeatRequested = false
    let reminder = reminders && now >= nextReminder
    let allowed = manual || escalation || assessment.kind == .fault || now >= nextSpeech
    guard allowed && (manual || escalation || spokenKey != key || reminder) else {
      return Output(phrase: nil, cancel: changed)
    }
    spokenKey = key
    nextSpeech = now + 3
    nextReminder = now + (assessment.kind == .fault ? 15 : (urgency == .urgent ? 6 : 12))
    if escalation { lastEscalation = now }
    let phrase = detailed ? assessment.speech : assessment.briefSpeech
    return Output(phrase: urgency == .urgent ? "Urgent. \(phrase)" : phrase,
                  cancel: changed || manual)
  }
}
