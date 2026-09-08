import SwiftUI

@main
struct YutoriInputProbeApp: App {
    @StateObject private var recorder: EventRecorder

    init() {
        let configuration: ProbeConfiguration
        do {
            configuration = try ProbeConfiguration.parse(arguments: CommandLine.arguments)
        } catch {
            let fallbackDirectory = FileManager.default.temporaryDirectory
                .appendingPathComponent("YutoriInputProbe", isDirectory: true)
            configuration = try! ProbeConfiguration.parse(
                arguments: [],
                defaultDirectory: fallbackDirectory,
                generatedSessionID: "configuration-error"
            )
        }
        _recorder = StateObject(wrappedValue: EventRecorder(configuration: configuration))
    }

    var body: some Scene {
        WindowGroup("Yutori Input Probe") {
            ContentView(recorder: recorder)
        }
        .defaultSize(width: 1120, height: 780)
        .windowResizability(.contentMinSize)
    }
}
