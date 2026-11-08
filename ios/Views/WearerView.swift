import SwiftUI

/// The default screen, for the person wearing the belt. One large Start/Stop
/// control and one sentence of state. Built for VoiceOver first.
struct WearerView: View {
    @ObservedObject var model: AppModel
    @ObservedObject var hub: HubSession
    @ObservedObject var bridge: BridgeModel
    @Environment(\.colorSchemeContrast) private var contrast
    @ScaledMetric(relativeTo: .largeTitle) private var actionFontSize: CGFloat = 44

    init(model: AppModel) {
        self.model = model
        hub = model.hub
        bridge = model.bridge
    }

    var body: some View {
        GeometryReader { viewport in
            let height = min(240, max(144, viewport.size.height * 0.32))
            if viewport.size.height >= 400 {
                VStack(spacing: 0) {
                    startStopButton(minHeight: height)
                        .padding([.top, .horizontal])
                    ScrollView { wearerControls.padding() }
                }
            } else {
                ScrollView {
                    VStack(spacing: 20) {
                        startStopButton(minHeight: height)
                        wearerControls
                    }
                    .padding()
                }
            }
        }
        .navigationTitle("Belt alerts")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                NavigationLink("Setup") { HelperView(model: model) }
                    .accessibilityHint("Opens hub pairing, glasses setup, and speech settings.")
            }
        }
    }

    private var wearerControls: some View {
        VStack(spacing: 20) {
            if let banner = model.simulationBanner {
                Label(banner, systemImage: "theatermasks.fill")
                    .font(.title3.weight(.heavy))
                    .foregroundStyle(.black)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(12)
                    .background(Color.orange, in: RoundedRectangle(cornerRadius: 12))
            }
            statusCard
            recoveryAction
            // Pause needs the hub control code; without it Stop is the only control.
            if model.isRunning && hub.hasControlCode { pauseButton }
            Text("Silence does not mean the path is clear.")
                .font(.title3.weight(.semibold))
                .multilineTextAlignment(.center)
                .frame(maxWidth: .infinity)
            if hub.settings?.record == true {
                Label("The hub is recording this session.", systemImage: "record.circle")
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(.red)
            }
            Button(action: model.readBeltStatus) {
                Label("Hear status", systemImage: "speaker.wave.2")
                    .frame(maxWidth: .infinity, minHeight: 44)
            }
            .buttonStyle(.bordered)
            .tint(.alertStart)
            .accessibilityHint("Reads the current alert status.")
            locationButton
            NavigationLink {
                PracticeView(model: model, hub: hub)
            } label: {
                Label("Practice alerts", systemImage: "waveform")
                    .frame(maxWidth: .infinity, minHeight: 44)
            }
            .buttonStyle(.bordered)
            .tint(.alertStart)
            .disabled(bridge.isSending)
            .accessibilityHint("Try sample alerts while stationary with live alerts off.")
        }
    }

    @ViewBuilder
    private var recoveryAction: some View {
        switch model.condition.kind {
        case .off where model.pairing == nil:
            recoveryLink("Set up hub connection", to: .hub)
        case .hubDisconnected, .statusStale, .stopNotConfirmed, .stopUndone:
            recoveryLink("Check hub connection", to: .hub)
        case .noCameraFrames, .stopped:
            recoveryLink("Check glasses connection", to: .glasses)
        case .beltLink, .beltNotResponding, .belt, .beltMode:
            recoveryLink("Check belt connection", to: .belt)
        case .fault(let reason), .pausedFault(let reason):
            if reason.contains("camera") {
                recoveryLink("Check glasses connection", to: .glasses)
            } else if reason.contains("belt") {
                recoveryLink("Check belt connection", to: .belt)
            } else {
                recoveryLink("Check hub connection", to: .hub)
            }
        case .diagnostic, .resumeStuck:
            recoveryLink("Open Setup", to: .setup)
        default:
            EmptyView()
        }
    }

    private func recoveryLink(_ title: String, to destination: HelperView.Destination) -> some View {
        NavigationLink {
            HelperView(model: model, destination: destination)
        } label: {
            Text(title).frame(maxWidth: .infinity, minHeight: 44)
        }
        .buttonStyle(.bordered)
        .tint(.alertStart)
    }

    private var locationButton: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button(action: model.findLocation) {
                Label("Where am I?", systemImage: "location")
                    .font(.body.weight(.semibold))
                    .frame(maxWidth: .infinity, minHeight: 44)
            }
            .buttonStyle(.bordered)
            .tint(.alertStart)
            .disabled(model.locating)
            .accessibilityInputLabels(["Where am I", "Find my location"])
            .accessibilityValue(model.locating ? "Finding location" : model.locationSummary ?? "")
            .accessibilityHint("Looks up your approximate location. Obstacle alerts take priority.")

            if let text = model.locating ? "Finding location…" : model.locationSummary {
                Text(text)
                    .foregroundStyle(.secondary)
                    .accessibilityHidden(true)
            }
        }
    }

    private var statusCard: some View {
        let condition = model.condition
        return VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Image(systemName: icon(condition.tone))
                    .font(.largeTitle.weight(.bold))
                    .accessibilityHidden(true)
                Text(condition.sentence)
                    .font(.largeTitle.weight(.bold))
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text(condition.detail)
                .font(.title3)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(20)
        .foregroundStyle(foreground(condition.tone))
        .background(background(condition.tone), in: RoundedRectangle(cornerRadius: 20))
        .overlay(
            RoundedRectangle(cornerRadius: 20)
                .strokeBorder(Color.primary, lineWidth: contrast == .increased ? 4 : 0))
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
        .accessibilityLabel(condition.sentence)
        .accessibilityValue(condition.detail)
    }

    private func startStopButton(minHeight: CGFloat) -> some View {
        let running = model.isRunning
        return Button(action: model.toggleAlerts) {
            Text(running ? "Stop alerts" : "Start alerts")
                .font(.system(size: actionFontSize, weight: .heavy, design: .rounded))
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
                .padding(16)
                .frame(maxWidth: .infinity, minHeight: minHeight)
        }
        .buttonStyle(BigButtonStyle(fill: running ? .alertStop : .alertStart, contrast: contrast))
        .accessibilityInputLabels(running ? ["Stop alerts", "Stop belt"] : ["Start alerts", "Start belt"])
        .accessibilityAction(named: Text("Read belt status")) { model.readBeltStatus() }
        .accessibilityHint(running
            ? "Ends this belt session."
            : "Starts the glasses camera and belt alerts.")
    }

    private var pauseButton: some View {
        let paused = model.isPaused
        return Button(action: model.togglePause) {
            Text(paused ? "Resume alerts" : "Pause alerts")
                .font(.largeTitle.weight(.bold))
                .multilineTextAlignment(.center)
                .padding(.horizontal, 12)
                .frame(maxWidth: .infinity, minHeight: 96)
        }
        .buttonStyle(BigButtonStyle(fill: Color(.darkGray), contrast: contrast))
        .accessibilityInputLabels(paused ? ["Resume alerts", "Resume belt"] : ["Pause alerts", "Pause belt"])
        .accessibilityHint(paused
            ? "Turns obstacle alerts back on. The belt plays the resume cue."
            : "Keeps the camera running but turns obstacle alerts off. The belt plays the pause cue, then pulses softly as a reminder.")
    }

    private func icon(_ tone: WearerTone) -> String {
        switch tone {
        case .ok: return "dot.radiowaves.left.and.right"
        case .warning: return "exclamationmark.triangle.fill"
        case .off: return "power.circle"
        }
    }

    // Warnings are black on yellow: loud on purpose. "Alerts on" is plain blue,
    // not a reassuring green, because it means "working", not "clear".
    private func background(_ tone: WearerTone) -> Color {
        switch tone {
        case .ok: return contrast == .increased ? Color(red: 0, green: 0.2, blue: 0.55) : Color(red: 0.05, green: 0.3, blue: 0.7)
        case .warning: return Color.yellow
        case .off: return Color(.secondarySystemBackground)
        }
    }

    private func foreground(_ tone: WearerTone) -> Color {
        switch tone {
        case .ok: return .white
        case .warning: return .black
        case .off: return .primary
        }
    }
}

private struct PracticeView: View {
    @ObservedObject var model: AppModel
    @ObservedObject var hub: HubSession
    @State private var visible = false
    @State private var result: String?

    private var canPlayBelt: Bool {
        let requestPending = hub.isPending { action in
            if case .practiceCue = action { return true }
            return false
        }
        return hub.link == .connected && hub.controlPairing == .paired && hub.status != nil
            && hub.status?.reason != "belt practice"
            && !requestPending
    }

    var body: some View {
        Form {
            Section {
                Text("Stop live alerts and stay still while you try the cues.")
                Button("Play sample warning") {
                    model.speakPractice("Practice. Caution. Person ahead, about four feet away.")
                }
            }
            Section {
                ForEach(PracticeCue.allCases, id: \.self) { cue in
                    Button("Play \(cue.label.lowercased())") { play(cue) }
                        .disabled(!canPlayBelt)
                }
                if let result { Text(result) }
            } header: {
                Text("Belt cues")
            } footer: {
                if hub.controlPairing != .paired || hub.link != .connected {
                    Text("Connect a hub and enter its control code in Setup to try the belt.")
                } else if hub.status?.belt == "simulated" {
                    Text("This hub has a simulated belt. You won’t feel a vibration.")
                } else {
                    Text("The left and right cues indicate where a hazard is. The connection-loss cue is two short pulses across the belt.")
                }
            }
        }
        .navigationTitle("Practice alerts")
        .onAppear { visible = true }
        .onDisappear {
            visible = false
            model.stopManualSpeech()
        }
    }

    private func play(_ cue: PracticeCue) {
        guard canPlayBelt else { return }
        result = nil
        hub.send(.practiceCue(cue)) { reply in
            guard visible else { return }
            let text = reply == .ok ? "Practice. \(cue.label)." : reply.message
            result = text
            model.speakPractice(text)
        }
    }
}

extension Color {
    static let alertStart = Color(red: 0.04, green: 0.29, blue: 0.64)
    static let alertStop = Color(red: 0.60, green: 0.12, blue: 0.17)
}

struct BigButtonStyle: ButtonStyle {
    let fill: Color
    let contrast: ColorSchemeContrast

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundStyle(.white)
            .background(fill.opacity(configuration.isPressed ? 0.7 : 1), in: RoundedRectangle(cornerRadius: 28))
            .overlay(
                RoundedRectangle(cornerRadius: 28)
                    .strokeBorder(Color.primary, lineWidth: contrast == .increased ? 4 : 0))
            .contentShape(RoundedRectangle(cornerRadius: 28))
    }
}
