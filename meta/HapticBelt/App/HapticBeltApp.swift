//
// HapticBeltApp.swift
//
// Phone side of the haptic belt: streams the Meta glasses camera to the hub
// laptop and shows what the belt is doing. See meta/README.md.
//

import MWDATCore
import SwiftUI

@main
struct HapticBeltApp: App {
  /// nil if the Meta SDK failed to configure. Touching Wearables.shared then would crash.
  @State private var model: AppModel?
  private let setupError: String?

  init() {
    do {
      try Wearables.configure()
      _model = State(initialValue: AppModel(wearables: Wearables.shared))
      setupError = nil
    } catch {
      NSLog("[HapticBelt] Wearables.configure failed: \(error)")
      _model = State(initialValue: nil)
      setupError = "\(error)"
    }
  }

  var body: some Scene {
    WindowGroup {
      if let model {
        ContentView(model: model)
          .onOpenURL { url in model.glasses.handleOpenURL(url) }
      } else {
        ContentUnavailableView(
          "Meta Wearables SDK didn't start",
          systemImage: "eyeglasses",
          description: Text(
            "\(setupError ?? "Unknown error"). Check the signing team in Config/Local.xcconfig and run on a real iPhone.")
        )
      }
    }
  }
}
