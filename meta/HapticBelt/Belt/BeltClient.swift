import CoreBluetooth
import Foundation
import Observation

/// UI state only. BLE callbacks and heartbeats run on their own serial queue.
@Observable @MainActor
final class BeltClient {
  struct Device: Identifiable, Equatable, Sendable {
    let id: UUID
    let name: String
    let rssi: Int
  }
  enum State: Equatable, Sendable { case idle, scanning, connecting, discovering, ready }
  private(set) var state: State = .idle
  private(set) var devices: [Device] = []
  private(set) var message = "Belt not connected"
  private(set) var selectedName = "None"
  private(set) var info: BeltInfo?
  private(set) var firmware: BeltFirmwareStatus?
  private(set) var confirmation = "Not connected"
  private(set) var acknowledgedWrites = 0
  private(set) var lastAckMs: Double?
  private(set) var deliveryIssue: String?
  var ready: Bool { state == .ready }
  var busy: Bool { state == .connecting || state == .discovering }
  private(set) var canRelay = false
  private(set) var maxCommandGapMs: Double?
  @ObservationIgnored var onFailure: ((String) -> Void)?
  @ObservationIgnored private var transport: BeltTransport?
  @ObservationIgnored private var revision: UInt64 = 0

  init() {
    transport = BeltTransport { [weak self] snapshot, failure in
      Task { @MainActor [weak self] in
        guard let self else { return }
        if snapshot.revision > self.revision { self.apply(snapshot) }
        if let failure { self.onFailure?(failure) }
      }
    }
  }

  func scan() { transport?.enqueue { $0.scan() } }
  func stopScan() { transport?.enqueue { $0.stopScan() } }
  func connect(_ id: UUID) { transport?.enqueue { $0.connect(id) } }
  func disconnect() { transport?.enqueue { $0.disconnect() } }
  func submit(_ command: BeltCommand, armed: Bool) {
    let producedAt = ProcessInfo.processInfo.systemUptime
    transport?.enqueue { $0.submit(command, armed: armed, producedAt: producedAt) }
  }
  func testMotor(_ index: Int) {
    let producedAt = ProcessInfo.processInfo.systemUptime
    transport?.enqueue { $0.testMotor(index, producedAt: producedAt) }
  }

  private func apply(_ snapshot: BeltTransport.Snapshot) {
    revision = snapshot.revision
    if state != snapshot.state { state = snapshot.state }
    if devices != snapshot.devices { devices = snapshot.devices }
    if message != snapshot.message { message = snapshot.message }
    if selectedName != snapshot.selectedName { selectedName = snapshot.selectedName }
    if info != snapshot.info { info = snapshot.info }
    if firmware != snapshot.firmware { firmware = snapshot.firmware }
    if confirmation != snapshot.confirmation { confirmation = snapshot.confirmation }
    if acknowledgedWrites != snapshot.acknowledgedWrites { acknowledgedWrites = snapshot.acknowledgedWrites }
    if lastAckMs != snapshot.lastAckMs { lastAckMs = snapshot.lastAckMs }
    if deliveryIssue != snapshot.deliveryIssue { deliveryIssue = snapshot.deliveryIssue }
    if canRelay != snapshot.canRelay { canRelay = snapshot.canRelay }
    if maxCommandGapMs != snapshot.maxCommandGapMs { maxCommandGapMs = snapshot.maxCommandGapMs }
  }
}

/// Unchecked Sendable because all mutable state and CoreBluetooth objects are
/// accessed exclusively on `queue`. Only Sendable snapshots cross to the UI.
final class BeltTransport: NSObject, CBCentralManagerDelegate, CBPeripheralDelegate, @unchecked Sendable {
  typealias Device = BeltClient.Device
  typealias State = BeltClient.State
  struct Snapshot: Sendable {
    let revision: UInt64
    let state: State
    let devices: [Device]
    let message: String
    let selectedName: String
    let info: BeltInfo?
    let firmware: BeltFirmwareStatus?
    let confirmation: String
    let acknowledgedWrites: Int
    let lastAckMs: Double?
    let deliveryIssue: String?
    let canRelay: Bool
    let maxCommandGapMs: Double?
  }
  private var state: State = .idle
  private var devices: [Device] = []
  private var message = "Belt not connected"
  private var selectedName = "None"
  private var info: BeltInfo?
  private var firmware: BeltFirmwareStatus?
  private var confirmation = "Not connected"
  private var acknowledgedWrites = 0
  private var lastAckMs: Double?
  private var deliveryIssue: String?
  var ready: Bool { state == .ready }
  var canRelay: Bool {
    ready && deliveryIssue == nil && acknowledgedWrites > 0
      && (statusMonitor == nil || statusMonitor?.confirmed == true)
  }
  var busy: Bool { state == .connecting || state == .discovering }
  private var central: CBCentralManager?
  private var found: [UUID: CBPeripheral] = [:]
  private var peripheral: CBPeripheral?
  private var commandCharacteristic: CBCharacteristic?
  private var statusCharacteristic: CBCharacteristic?
  private var statusSubscribed = false
  private var scanRequested = false
  private var deadline: Double?
  private var gate = BeltWriteGate()
  private var statusMonitor: BeltStatusMonitor?
  private var intent = BeltIntent.off
  private var issueAt: Double?
  private let queue = DispatchQueue(label: "com.hackutd26.belt", qos: .userInitiated)
  private var heartbeat: DispatchSourceTimer?
  private let onUpdate: @Sendable (Snapshot, String?) -> Void
  private var revision: UInt64 = 0
  private var nextSnapshotAt = 0.0
  private var now: Double { ProcessInfo.processInfo.systemUptime }

  init(onUpdate: @escaping @Sendable (Snapshot, String?) -> Void) {
    self.onUpdate = onUpdate
    super.init()
    queue.async { [weak self] in
      guard let self else { return }
      let timer = DispatchSource.makeTimerSource(queue: self.queue)
      timer.schedule(deadline: .now(), repeating: .milliseconds(25), leeway: .milliseconds(2))
      timer.setEventHandler { [weak self] in self?.tick() }
      self.heartbeat = timer
      timer.resume()
    }
  }
  deinit { heartbeat?.cancel() }

  /// Every public operation and delegate callback is confined to this queue.
  func enqueue(_ operation: @escaping @Sendable (BeltTransport) -> Void) {
    queue.async { [weak self] in
      guard let self else { return }
      operation(self)
    }
  }

  private func publish(failure: String? = nil) {
    dispatchPrecondition(condition: .onQueue(queue))
    guard failure != nil || now >= nextSnapshotAt else { return }
    nextSnapshotAt = now + 0.2
    revision &+= 1
    onUpdate(Snapshot(revision: revision, state: state, devices: devices,
      message: message, selectedName: selectedName, info: info, firmware: firmware,
      confirmation: confirmation, acknowledgedWrites: acknowledgedWrites,
      lastAckMs: lastAckMs, deliveryIssue: deliveryIssue, canRelay: canRelay,
      maxCommandGapMs: gate.maxCommandGapMs), failure)
  }

  func scan() {
    guard peripheral == nil else { return }
    scanRequested = true
    if central == nil { central = CBCentralManager(delegate: self, queue: queue) }
    else { startScanIfPowered() }
  }
  func stopScan() {
    scanRequested = false
    central?.stopScan()
    if state == .scanning { state = .idle; deadline = nil }
  }
  private func startScanIfPowered() {
    guard let central, central.state == .poweredOn else {
      message = "Waiting for Bluetooth. Check iPhone Bluetooth and app permission."
      return
    }
    devices = []
    found = [:]
    state = .scanning
    message = "Looking for HapticBelt…"
    deadline = now + 8
    central.scanForPeripherals(withServices: [CBUUID(string: BeltProtocol.service)], options: nil)
  }
  func connect(_ id: UUID) {
    guard let central, central.state == .poweredOn, peripheral == nil, let target = found[id] else { return }
    stopScan()
    peripheral = target
    target.delegate = self
    selectedName = target.name ?? "HapticBelt"
    state = .connecting
    message = "Connecting to \(selectedName)…"
    deadline = now + 10
    central.connect(target, options: nil)
  }

  func submit(_ command: BeltCommand, armed: Bool, producedAt: Double) {
    intent = armed && canRelay ? BeltIntent(command: command, expiresAt: producedAt + 0.2, expired: .fault) : .off
  }
  func testMotor(_ index: Int, producedAt: Double) {
    guard canRelay, (0..<5).contains(index) else { return }
    var levels = [0, 0, 0, 0, 0]
    levels[index] = 120
    intent = BeltIntent(command: BeltCommand.levels(levels)!, expiresAt: producedAt + 0.2, expired: .off)
    message = "Testing motor \(index) for 200 ms at level 120"
  }
  func disconnect() {
    tearDown("Disconnected. Firmware may play its link-loss cue before going quiet.")
  }
  private func fail(_ reason: String) {
    tearDown(reason)
    publish(failure: reason)
  }
  private func suspendDelivery(_ reason: String) {
    guard deliveryIssue == nil else { return }
    deliveryIssue = reason
    issueAt = now
    gate.markUnreliable()
    intent = .off
    confirmation = "Delivery unconfirmed; belt alerts stopped"
    message = reason + ". Keeping BLE connected and recovering with off commands."
    publish(failure: reason)
  }
  private func recoverIfConfirmed() {
    guard let issueAt, gate.healthyWrites >= 3,
      statusMonitor == nil || (statusMonitor?.hasMatched(since: issueAt) == true && statusMonitor?.fault(now: now) == nil) else { return }
    deliveryIssue = nil
    self.issueAt = nil
    message = "Delivery recovered. Re-enable belt alerts when ready."
    confirmation = statusMonitor == nil ? "Writes acknowledged; firmware status unavailable" : "Firmware reports commands applied"
  }
  private func tearDown(_ reason: String) {
    stopScan()
    intent = .off
    // Best effort only. If a prior write is in flight, do not append another.
    // Disconnect / TTL is the firmware's fallback; never claim silent shutdown.
    if let p = peripheral {
      if let characteristic = commandCharacteristic, gate.pending == nil, ready, p.state == .connected {
        p.writeValue(BeltCommand.off.encode(seq: 0), for: characteristic, type: .withResponse)
      }
      central?.cancelPeripheralConnection(p)
      p.delegate = nil
    }
    peripheral = nil
    commandCharacteristic = nil
    statusCharacteristic = nil
    statusSubscribed = false
    info = nil
    firmware = nil
    statusMonitor = nil
    gate = BeltWriteGate()
    acknowledgedWrites = 0
    deliveryIssue = nil
    issueAt = nil
    deadline = nil
    state = .idle
    message = reason
    confirmation = "Not connected"
  }
  private func tick() {
    dispatchPrecondition(condition: .onQueue(queue))
    defer { publish() }
    if let deadline, now >= deadline {
      if state == .scanning {
        stopScan()
        message = devices.isEmpty ? "No belt found. Power it on and disconnect its laptop BLE link." : "Select your belt below."
      } else if busy { fail("Belt connection or discovery timed out") }
    }
    guard ready, let p = peripheral, let characteristic = commandCharacteristic else { return }
    if gate.timedOut(now: now) { fail("Belt write has been unacknowledged for 1 second"); return }
    if statusMonitor?.stalled(now: now) == true { fail("No belt status for 3 seconds"); return }
    if gate.deliveryOverdue(now: now) {
      suspendDelivery(gate.pending == nil ? "Belt heartbeat ran late" : "Belt acknowledgement is delayed")
    }
    if let fault = statusMonitor?.fault(now: now) { suspendDelivery(fault) }
    if let write = gate.take(intent.current(now: now), now: now) {
      p.writeValue(write.command.encode(seq: write.seq), for: characteristic, type: .withResponse)
    }
  }

  func centralManagerDidUpdateState(_ central: CBCentralManager) {
    switch central.state {
    case .poweredOn:
      if scanRequested { startScanIfPowered() }
    case .unknown, .resetting:
      if peripheral != nil { fail("Bluetooth is restarting") }
    case .unauthorized: fail("Bluetooth permission denied. Enable it for Haptic Belt in Settings.")
    case .poweredOff: fail("Bluetooth is off")
    case .unsupported: fail("Bluetooth LE is unavailable on this device")
    @unknown default: fail("Bluetooth is unavailable")
    }
  }
  func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral,
                      advertisementData: [String: Any], rssi RSSI: NSNumber) {
    guard state == .scanning else { return }
    found[peripheral.identifier] = peripheral
    let device = Device(id: peripheral.identifier,
      name: (advertisementData[CBAdvertisementDataLocalNameKey] as? String) ?? peripheral.name ?? "HapticBelt",
      rssi: RSSI.intValue)
    devices.removeAll { $0.id == device.id }
    devices.append(device)
  }
  func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
    guard self.peripheral === peripheral else { central.cancelPeripheralConnection(peripheral); return }
    state = .discovering
    message = "Checking belt protocol and motor count…"
    peripheral.discoverServices([CBUUID(string: BeltProtocol.service)])
  }
  func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
    guard self.peripheral === peripheral else { return }
    fail("Could not connect to belt: \(error?.localizedDescription ?? "connection failed")")
  }
  func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
    guard self.peripheral === peripheral else { return }
    fail("Belt disconnected. Reconnect and re-enable belt alerts.")
  }
  func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
    guard self.peripheral === peripheral else { return }
    guard error == nil, let service = peripheral.services?.first(where: { $0.uuid == CBUUID(string: BeltProtocol.service) }) else {
      fail("Belt service is missing"); return
    }
    peripheral.discoverCharacteristics([BeltProtocol.command, BeltProtocol.info, BeltProtocol.status].map(CBUUID.init(string:)), for: service)
  }
  func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
    guard self.peripheral === peripheral else { return }
    let chars = service.characteristics ?? []
    guard error == nil,
      let command = chars.first(where: { $0.uuid == CBUUID(string: BeltProtocol.command) }), command.properties.contains(.write),
      let info = chars.first(where: { $0.uuid == CBUUID(string: BeltProtocol.info) }), info.properties.contains(.read) else {
      fail("Belt needs v1 info and acknowledged command writes"); return
    }
    commandCharacteristic = command
    statusCharacteristic = chars.first { $0.uuid == CBUUID(string: BeltProtocol.status) }
    if let status = statusCharacteristic {
      guard status.properties.contains(.notify) else { fail("Belt status does not support notifications"); return }
      peripheral.setNotifyValue(true, for: status)
    }
    peripheral.readValue(for: info)
  }
  func peripheral(_ peripheral: CBPeripheral, didUpdateNotificationStateFor characteristic: CBCharacteristic, error: Error?) {
    guard self.peripheral === peripheral, characteristic === statusCharacteristic else { return }
    guard error == nil, characteristic.isNotifying else { fail("Could not subscribe to belt status"); return }
    statusSubscribed = true
    finishDiscovery()
  }
  private func finishDiscovery() {
    guard state == .discovering, info != nil, commandCharacteristic != nil,
      statusCharacteristic == nil || statusSubscribed else { return }
    gate = BeltWriteGate()
    lastAckMs = nil
    deliveryIssue = nil
    issueAt = nil
    intent = .off
    statusMonitor = statusCharacteristic == nil ? nil : BeltStatusMonitor(startedAt: now)
    deadline = nil
    state = .ready
    message = "Connected. Belt alerts are off."
    confirmation = "Waiting for write acknowledgement"
    tick()
  }
  func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
    guard self.peripheral === peripheral else { return }
    guard error == nil, let data = characteristic.value else { fail("Could not read belt information"); return }
    if characteristic.uuid == CBUUID(string: BeltProtocol.info) {
      guard let decoded = BeltInfo.decode(data) else { fail("Incompatible belt: expected protocol v1 and five motors"); return }
      info = decoded
      finishDiscovery()
    } else if characteristic.uuid == CBUUID(string: BeltProtocol.status) {
      guard let decoded = BeltFirmwareStatus.decode(data) else { fail("Malformed belt status"); return }
      firmware = decoded
      if ready {
        statusMonitor?.receive(decoded, gate: gate, now: now)
        if let reason = statusMonitor?.fault(now: now) { suspendDelivery(reason) }
        recoverIfConfirmed()
        if deliveryIssue == nil && statusMonitor?.confirmed == true { confirmation = "Firmware reports commands applied" }
      }
    }
  }
  func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic, error: Error?) {
    guard self.peripheral === peripheral, characteristic.uuid == CBUUID(string: BeltProtocol.command), ready else { return }
    guard !gate.timedOut(now: now) else { fail("Belt acknowledgement arrived after the 1 second write timeout"); return }
    guard error == nil else { fail("Belt write failed: \(error!.localizedDescription)"); return }
    if gate.deliveryOverdue(now: now) { suspendDelivery("Belt acknowledgement is delayed") }
    gate.ack(now: now)
    acknowledgedWrites = gate.acknowledged
    lastAckMs = gate.lastAckMs
    recoverIfConfirmed()
    if deliveryIssue == nil && statusMonitor?.confirmed != true {
      confirmation = statusCharacteristic == nil ? "Writes acknowledged; firmware status unavailable" : "Writes acknowledged; waiting for firmware status"
    }
  }
}
