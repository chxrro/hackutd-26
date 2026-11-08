import SwiftUI

@main
struct CameraBridgeApp: App {
    @StateObject private var model = AppModel()
    @Environment(\.scenePhase) private var phase

    var body: some Scene {
        WindowGroup {
            NavigationStack {
                WearerView(model: model)
            }
            .onChange(of: phase) { _, newPhase in
                if newPhase == .background { model.appWentToBackground() }
            }
            .onOpenURL(perform: model.bridge.handleCallback)
        }
    }
}
