import SwiftUI

struct ContentView: View {
    @ObservedObject var recorder: EventRecorder

    private let scenarios = ["Freeform", "Keyboard", "Pointer", "Background", "Fallback", "Window recovery"]

    var body: some View {
        VStack(spacing: 14) {
            header
            Divider()
            HStack(alignment: .top, spacing: 14) {
                inputColumn
                pointerColumn
                stateColumn
            }
            Divider()
            eventTimeline
        }
        .padding(16)
        .frame(minWidth: 1040, minHeight: 720)
        .background(Color(nsColor: .windowBackgroundColor))
        .onAppear { recorder.install() }
    }

    private var header: some View {
        VStack(spacing: 8) {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Yutori Input Probe")
                        .font(.title2.weight(.semibold))
                    Text("Session \(recorder.configuration.sessionID)")
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                }
                Spacer()
                StateBadge(label: "APP", value: recorder.latestState.appActive ? "ACTIVE" : "INACTIVE", good: recorder.latestState.appActive)
                StateBadge(label: "KEY", value: recorder.latestState.windowKey ? "YES" : "NO", good: recorder.latestState.windowKey)
                StateBadge(label: "VISIBLE", value: recorder.latestState.windowVisible ? "YES" : "NO", good: recorder.latestState.windowVisible)
                Button("Clear") { recorder.reset() }
                    .accessibilityIdentifier("probe.clear")
            }
            HStack(spacing: 10) {
                Text("Scenario")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                Picker("Scenario", selection: Binding(
                    get: { recorder.armedScenario },
                    set: { recorder.arm($0) }
                )) {
                    ForEach(scenarios, id: \.self, content: Text.init)
                }
                .labelsHidden()
                .pickerStyle(.segmented)
            }
        }
    }

    private var inputColumn: some View {
        GroupBox("Keyboard & text") {
            VStack(alignment: .leading, spacing: 9) {
                Text("The raw key sink requests first responder without activating the app.")
                    .font(.caption)
                    .foregroundStyle(.secondary)

                ProbeKeySink(recorder: recorder)
                    .frame(height: 150)
                    .reportProbeGeometry("keySink", recorder: recorder)

                HStack {
                    ShortcutChip(keys: "⌘ K", label: "Command")
                    ShortcutChip(keys: "⌘ ⇧ K", label: "Shift")
                    ShortcutChip(keys: "⌃ K", label: "Control")
                    ShortcutChip(keys: "⌥ K", label: "Option")
                }

                Text("Try: Tab, ⇧Tab, Return, Escape, arrows, delete, home/end, page keys, and F1–F12.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(8)
        }
        .frame(maxWidth: .infinity)
    }

    private var pointerColumn: some View {
        GroupBox("Pointer & scroll") {
            VStack(spacing: 9) {
                LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 8) {
                    ForEach(1...4, id: \.self) { index in
                        Button("Target \(index)  ·  \(recorder.clickCounts["target-\(index)", default: 0])") {
                            recorder.recordClick(target: "target-\(index)")
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(index.isMultiple(of: 2) ? .teal : .indigo)
                        .controlSize(.large)
                        .accessibilityIdentifier("probe.pointer.target\(index)")
                        .reportProbeGeometry("target-\(index)", recorder: recorder)
                    }
                }

                DragProbe(recorder: recorder)

                ScrollView {
                    VStack(spacing: 5) {
                        ForEach(1...12, id: \.self) { row in
                            Text("Scroll marker \(row)")
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.horizontal, 8)
                                .padding(.vertical, 3)
                                .background(row.isMultiple(of: 2) ? Color.secondary.opacity(0.08) : .clear)
                        }
                    }
                }
                .frame(height: 80)
                .background(Color(nsColor: .controlBackgroundColor))
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .accessibilityIdentifier("probe.scroll.area")
            }
            .padding(8)
        }
        .frame(maxWidth: .infinity)
    }

    private var stateColumn: some View {
        GroupBox("Delivery evidence") {
            VStack(alignment: .leading, spacing: 8) {
                StateRow(label: "Frontmost", value: recorder.latestState.frontmostBundleID ?? "unknown")
                StateRow(label: "Window", value: recorder.latestState.windowTitle ?? "none")
                StateRow(label: "Number", value: recorder.latestState.windowNumber.map(String.init) ?? "none")
                StateRow(label: "Main", value: String(recorder.latestState.windowMain))
                StateRow(label: "Minimized", value: String(recorder.latestState.windowMiniaturized))
                StateRow(label: "Occluded", value: String(recorder.latestState.windowOccluded))
                StateRow(label: "Responder", value: recorder.latestState.firstResponder ?? "none")
                Divider()
                Text("Log")
                    .font(.caption.weight(.semibold))
                Text(recorder.configuration.logURL.path)
                    .font(.caption2.monospaced())
                    .textSelection(.enabled)
                    .lineLimit(4)
                if let error = recorder.loggingError {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .font(.caption)
                        .foregroundStyle(.red)
                }
                Spacer(minLength: 0)
            }
            .padding(8)
        }
        .frame(width: 260)
    }

    private var eventTimeline: some View {
        GroupBox("Received events · newest first") {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 5) {
                    ForEach(recorder.events.reversed()) { event in
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Text("#\(event.sequence)")
                                .foregroundStyle(.secondary)
                                .frame(width: 44, alignment: .trailing)
                            Text(event.category.uppercased())
                                .font(.caption2.weight(.bold))
                                .foregroundStyle(.indigo)
                                .frame(width: 78, alignment: .leading)
                            Text(event.name)
                                .fontWeight(.medium)
                                .frame(width: 190, alignment: .leading)
                            Text(detailSummary(event.details))
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                            Spacer()
                            Text(event.state.appActive ? "active" : "background")
                                .font(.caption2.monospaced())
                                .foregroundStyle(event.state.appActive ? .green : .orange)
                        }
                        .font(.caption.monospaced())
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(minHeight: 185)
            .padding(8)
        }
        .accessibilityIdentifier("probe.events.timeline")
    }

    private func detailSummary(_ details: [String: String]) -> String {
        details.keys.sorted().map { "\($0)=\(details[$0]!)" }.joined(separator: "  ")
    }
}

private struct StateBadge: View {
    let label: String
    let value: String
    let good: Bool

    var body: some View {
        VStack(spacing: 1) {
            Text(label).font(.caption2.weight(.bold))
            Text(value).font(.caption.monospaced().weight(.semibold))
        }
        .foregroundStyle(good ? Color.green : Color.orange)
        .padding(.horizontal, 9)
        .padding(.vertical, 5)
        .background((good ? Color.green : Color.orange).opacity(0.1), in: RoundedRectangle(cornerRadius: 8))
    }
}

private struct StateRow: View {
    let label: String
    let value: String

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label).foregroundStyle(.secondary)
            Spacer()
            Text(value)
                .font(.caption.monospaced())
                .lineLimit(2)
                .multilineTextAlignment(.trailing)
        }
        .font(.caption)
    }
}

private struct ShortcutChip: View {
    let keys: String
    let label: String

    var body: some View {
        VStack(spacing: 2) {
            Text(keys).font(.caption.monospaced().weight(.semibold))
            Text(label).font(.caption2).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 6)
        .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 7))
    }
}
