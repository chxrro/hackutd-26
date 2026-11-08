import SwiftUI

/// The default screen, for the person wearing the belt. One large Start/Stop
/// control and one sentence of state. Built for VoiceOver first.
struct WearerView: View {
    @ObservedObject var model: AppModel
    @ObservedObject var hub: HubSession
    @ObservedObject var bridge: BridgeModel
    @Environment(\.colorSchemeContrast) private var contrast

    init(model: AppModel) {
        self.model = model
        hub = model.hub
        bridge = model.bridge
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                if let banner = model.simulationBanner {
                    Label(banner, systemImage: "theatermasks.fill")
                        .font(.title3.weight(.heavy))
                        .foregroundStyle(.black)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(12)
                        .background(Color.orange, in: RoundedRectangle(cornerRadius: 12))
                        .accessibilitySortPriority(4)
                }
                statusCard
                startStopButton
                // Pause needs the hub control code; without it Stop is the only control.
                if model.isRunning && hub.hasControlCode { pauseButton }
                Text("Silence does not mean the path is clear.")
                    .font(.title3.weight(.semibold))
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: .infinity)
                    .accessibilitySortPriority(1)
                if hub.settings?.record == true {
                    Label("The hub is recording this session.", systemImage: "record.circle")
                        .font(.title3.weight(.semibold))
                        .foregroundStyle(.red)
                }
            }
            .padding()
        }
        .navigationTitle("Belt alerts")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                NavigationLink("Setup") { HelperView(model: model) }
                    .accessibilityHint("Pairing, glasses and hub settings, for a helper.")
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
        .accessibilityLabel("\(condition.sentence). \(condition.detail)")
        .accessibilitySortPriority(3)
    }

    private var startStopButton: some View {
        let running = model.isRunning
        return Button(action: model.toggleAlerts) {
            Text(running ? "Stop" : "Start")
                .font(.system(size: 64, weight: .heavy, design: .rounded))
                .minimumScaleFactor(0.5)
                .frame(maxWidth: .infinity, minHeight: 220)
        }
        .buttonStyle(BigButtonStyle(fill: running ? Color.red : Color.blue, contrast: contrast))
        .accessibilityLabel(running ? "Stop alerts" : "Start alerts")
        .accessibilityHint(running
            ? "Turns belt alerts off."
            : "Starts the glasses camera and belt alerts.")
        .accessibilitySortPriority(2)
    }

    private var pauseButton: some View {
        let paused = model.isPaused
        return Button(action: model.togglePause) {
            Text(paused ? "Resume" : "Pause")
                .font(.largeTitle.weight(.bold))
                .frame(maxWidth: .infinity, minHeight: 96)
        }
        .buttonStyle(BigButtonStyle(fill: Color(.darkGray), contrast: contrast))
        .accessibilityLabel(paused ? "Resume alerts" : "Pause alerts")
        .accessibilityHint(paused
            ? "Turns belt alerts back on."
            : "Keeps the camera running but turns belt alerts off.")
        .accessibilitySortPriority(1.5)
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
