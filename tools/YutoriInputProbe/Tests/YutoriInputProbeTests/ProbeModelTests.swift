import AppKit
import Foundation
import Testing
@testable import YutoriInputProbe

@Suite("Probe model tests")
struct ProbeModelTests {
    @Test("Configuration accepts explicit session and log path")
    func configurationArguments() throws {
        let configuration = try ProbeConfiguration.parse(arguments: [
            "YutoriInputProbe",
            "--session-id", "session-7",
            "--log-path", "/tmp/probe-session.jsonl",
        ])

        #expect(configuration.sessionID == "session-7")
        #expect(configuration.logURL.path == "/tmp/probe-session.jsonl")
    }

    @Test("Configuration rejects malformed arguments")
    func configurationErrors() {
        #expect(throws: ProbeConfigurationError.missingValue("--session-id")) {
            try ProbeConfiguration.parse(arguments: ["app", "--session-id"])
        }
        #expect(throws: ProbeConfigurationError.unknownArgument("--wat")) {
            try ProbeConfiguration.parse(arguments: ["app", "--wat"])
        }
    }

    @Test("Chord formatter uses macOS modifier vocabulary")
    func chordFormatting() {
        let flags: NSEvent.ModifierFlags = [.command, .shift]
        #expect(ProbeKeyFormatter.chord(charactersIgnoringModifiers: "k", flags: flags) == "shift+cmd+k")
        #expect(ProbeKeyFormatter.displayKey("\r") == "enter")
        #expect(ProbeKeyFormatter.displayKey("\t") == "tab")
        #expect(ProbeKeyFormatter.displayKey("\u{1B}") == "escape")
        #expect(ProbeEventTypeFormatter.name(.keyDown) == "keyDown")
        #expect(ProbeEventTypeFormatter.name(.scrollWheel) == "scrollWheel")
    }

    @Test("JSON event encoding is one newline-delimited object")
    func eventEncoding() throws {
        let state = ProbeStateSnapshot(
            appActive: false,
            appHidden: false,
            frontmostBundleID: "com.example.frontmost",
            windowNumber: 4,
            windowTitle: "Probe",
            windowKey: false,
            windowMain: false,
            windowVisible: true,
            windowMiniaturized: false,
            windowOccluded: true,
            firstResponder: "NSTextView"
        )
        let event = ProbeEvent(
            id: UUID(uuidString: "00000000-0000-0000-0000-000000000001")!,
            sessionID: "s1",
            sequence: 2,
            timestamp: "2026-09-07T00:00:00Z",
            monotonicNanoseconds: 42,
            category: "command",
            name: "cmd+k",
            details: ["source": "menu"],
            state: state
        )

        let data = try ProbeEventEncoding.jsonLine(event)
        #expect(data.last == 0x0A)
        let decoded = try JSONDecoder().decode(ProbeEvent.self, from: data.dropLast())
        #expect(decoded == event)
    }
}
