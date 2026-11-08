import CoreLocation
import MapKit

/// Resolves one location only when the wearer asks. The result stays in memory.
/// Core Location delivers callbacks on the run loop where the manager was created.
@MainActor
final class LocationLookup: NSObject, @preconcurrency CLLocationManagerDelegate {
    private var manager: CLLocationManager?
    private var completion: ((String) -> Void)?
    private var geocoding: Task<Void, Never>?
    private var timeout: Task<Void, Never>?
    private var requestedFix = false

    func start(allowPermissionPrompt: Bool, completion: @escaping (String) -> Void) {
        cancel()
        let manager = CLLocationManager()
        self.manager = manager
        self.completion = completion
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyNearestTenMeters

        if manager.authorizationStatus == .notDetermined {
            if allowPermissionPrompt {
                manager.requestWhenInUseAuthorization()
            } else {
                finish("Set up location permission before starting alerts.")
            }
        } else {
            locationManagerDidChangeAuthorization(manager)
        }
    }

    func cancel() {
        completion = nil
        timeout?.cancel()
        timeout = nil
        geocoding?.cancel()
        geocoding = nil
        manager?.delegate = nil
        manager?.stopUpdatingLocation()
        manager = nil
        requestedFix = false
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        guard self.manager === manager else { return }
        switch manager.authorizationStatus {
        case .authorizedAlways, .authorizedWhenInUse:
            guard !requestedFix else { return }
            requestedFix = true
            timeout = Task { [weak self] in
                do {
                    try await Task.sleep(for: .seconds(15))
                    self?.finish("Couldn’t get your location. Try again.")
                } catch {}
            }
            manager.requestLocation()
        case .denied, .restricted:
            finish("Location is unavailable. Check Wayfeel’s location permission in Settings.")
        case .notDetermined:
            break
        @unknown default:
            finish("Location is unavailable.")
        }
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard self.manager === manager, geocoding == nil else { return }
        guard let location = locations.last,
              CLLocationCoordinate2DIsValid(location.coordinate),
              (0...20_000).contains(location.horizontalAccuracy),
              abs(location.timestamp.timeIntervalSinceNow) <= 20 else {
            finish("Couldn’t get a current location. Try again.")
            return
        }
        let streetLevel = manager.accuracyAuthorization == .fullAccuracy && location.horizontalAccuracy <= 100
        geocoding = Task { [weak self] in
            let place = await Self.place(for: location, streetLevel: streetLevel)
            guard !Task.isCancelled else { return }
            self?.finish(place.map { "You’re near \($0)." } ?? "Couldn’t find a place name. Try again.")
        }
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        guard self.manager === manager else { return }
        finish("Couldn’t get your location. Try again.")
    }

    private func finish(_ message: String) {
        let reply = completion
        cancel()
        reply?(message)
    }

    private static func place(for location: CLLocation, streetLevel: Bool) async -> String? {
        if #available(iOS 26, *) {
            guard let request = MKReverseGeocodingRequest(location: location),
                  let item = try? await request.mapItems.first else { return nil }
            let area = item.addressRepresentations?.cityWithContext
            return streetLevel ? (item.address?.shortAddress ?? area) : area
        }
        guard let place = try? await CLGeocoder().reverseGeocodeLocation(location).first else { return nil }
        let parts = streetLevel ? [place.thoroughfare, place.locality] : [place.locality, place.administrativeArea]
        let description = parts.compactMap { $0 }.joined(separator: ", ")
        return description.isEmpty ? nil : description
    }
}
