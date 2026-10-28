// The Ask tab: the connect screen until you agree to use Claude or Codex, then a chat. All behaviour is in ChatModel.
import AppKit
import SwiftUI

/// Where the search box is on the other tabs: who you are talking to, and the chat's own buttons.
struct AskHeader: View {
    @ObservedObject var model: AppModel
    @ObservedObject var chat: ChatModel

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "sparkles").foregroundColor(.accentColor)
            if chat.connected, let e = chat.engine {
                Text(e.label).font(.system(size: 14, weight: .semibold))
                if let m = chat.status?.model, chat.status?.engine == e.rawValue {
                    Text(m).font(.system(size: 11)).foregroundColor(.secondary)
                }
                Spacer()
                IconButton(symbol: "square.and.pencil", help: "Start a new chat") { chat.clear() }
                    .disabled(!chat.hasConversation)
                Menu {
                    let other = e.other
                    if chat.status?.engines[other.rawValue]?.usable == true {
                        Button("Use \(other.label) instead") { chat.connect(other) }
                    }
                    Button("What is sent to \(e.label)…") { Alerts.info("What \(e.label) is sent", chat.disclosure(e)) }
                    Divider()
                    Button("Disconnect…") { chat.disconnect() }
                } label: {
                    Image(systemName: "ellipsis.circle").font(.system(size: 13))
                }
                .menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize()
            } else {
                Text("Ask Claude or Codex").font(.system(size: 14, weight: .semibold))
                Spacer()
            }
        }
        .padding(.horizontal, 12).padding(.vertical, 10)
        .frame(height: 40)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.primary.opacity(0.07)))
        .padding(.horizontal, 12).padding(.top, 12).padding(.bottom, 8)
    }
}

struct AskView: View {
    @ObservedObject var model: AppModel
    @ObservedObject var chat: ChatModel
    var close: () -> Void = {}

    var body: some View {
        if !chat.supported {
            VStack(spacing: 8) {
                Image(systemName: "exclamationmark.triangle").font(.system(size: 26)).foregroundColor(.secondary)
                Text("This chainlog cannot chat yet").font(.system(size: 13, weight: .semibold))
                Text("Run: chainlog setup menubar").font(.system(size: 12)).foregroundColor(.secondary)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity).padding(30)
        } else if chat.connected {
            ChatView(model: model, chat: chat, close: close)
        } else {
            ConnectView(chat: chat)
        }
    }
}

// MARK: - connecting

struct ConnectView: View {
    @ObservedObject var chat: ChatModel

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                Image(systemName: "sparkles").font(.system(size: 28)).foregroundColor(.accentColor).padding(.top, 14)
                Text("Chat about your sessions").font(.system(size: 15, weight: .semibold))
                Text("Ask what you worked on, how a PR ended, or find a session by what happened in it. It uses the Claude or Codex you are already signed in to: no keys, no extra account.")
                    .font(.system(size: 12.5)).foregroundColor(.secondary).multilineTextAlignment(.center).padding(.horizontal, 22)
                if let s = chat.status {
                    ForEach(AskEngine.allCases) { e in EngineCard(chat: chat, engine: e, status: s.engines[e.rawValue]) }
                } else if chat.checking {
                    ProgressView("Looking for Claude and Codex…").controlSize(.small).padding(.vertical, 18)
                } else {
                    Text("Could not check what is installed.").font(.system(size: 12)).foregroundColor(.orange).padding(.top, 8)
                }
                Text("Connecting sends your questions, and the titles, notes and short excerpts of the sessions chainlog picks, to that tool. Secrets are redacted first, and it gets no files, shell or other tools. You see the details before anything is connected.")
                    .font(.system(size: 11)).foregroundColor(.secondary.opacity(0.9)).multilineTextAlignment(.center).padding(.horizontal, 26)
                Button("Check again") { chat.loadStatus() }.buttonStyle(.plain).font(.system(size: 12)).foregroundColor(.accentColor)
                    .disabled(chat.checking).padding(.bottom, 12)
            }
            .frame(maxWidth: .infinity)
        }
        .onAppear { if chat.status == nil { chat.loadStatus() } }
    }
}

struct EngineCard: View {
    @ObservedObject var chat: ChatModel
    let engine: AskEngine
    let status: AskEngineStatus?

    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(engine.label).font(.system(size: 13, weight: .semibold))
                Text(detail).font(.system(size: 11.5)).foregroundColor(color).lineLimit(2).fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            if chat.connecting == engine {
                ProgressView().controlSize(.small)
            } else {
                Button("Connect") { chat.connect(engine) }
                    .disabled(!(status?.usable ?? false) || chat.connecting != nil)
            }
        }
        .padding(.horizontal, 12).padding(.vertical, 10)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.primary.opacity(0.06)))
        .padding(.horizontal, 16)
    }

    var detail: String { status?.detail ?? "could not check" }
    var color: Color { status?.usable == true ? .green : (status?.installed == true ? .orange : .secondary) }
}

// MARK: - the chat

struct ChatView: View {
    @ObservedObject var model: AppModel
    @ObservedObject var chat: ChatModel
    var close: () -> Void
    @FocusState private var focused: Bool

    var body: some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 10) {
                        if !chat.hasConversation { Suggestions(chat: chat) }
                        ForEach(chat.messages) { m in MessageView(model: model, chat: chat, message: m) }
                        if chat.thinking { Thinking(chat: chat) }
                        Color.clear.frame(height: 1).id("end")
                    }
                    .padding(.horizontal, 12).padding(.vertical, 8)
                }
                .onChange(of: chat.messages.count) { _ in scrollToEnd(proxy) }
                .onChange(of: chat.thinking) { _ in scrollToEnd(proxy) }
                .onAppear { scrollToEnd(proxy, animated: false) }  // reopening the popover shows the latest message, not the first
            }
            Divider()
            Composer(chat: chat, focused: $focused, close: close)
        }
        .onAppear { DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) { focused = true } }
    }

    func scrollToEnd(_ proxy: ScrollViewProxy, animated: Bool = true) {
        DispatchQueue.main.async {
            if animated { withAnimation(.easeOut(duration: 0.15)) { proxy.scrollTo("end", anchor: .bottom) } } else { proxy.scrollTo("end", anchor: .bottom) }
        }
    }
}

struct Suggestions: View {
    @ObservedObject var chat: ChatModel
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Ask about what you worked on, or search with details").font(.system(size: 12)).foregroundColor(.secondary)
            ForEach(ChatModel.suggestions, id: \.self) { s in
                Button { chat.send(s) } label: {
                    Text(s).font(.system(size: 12.5)).foregroundColor(.primary)
                        .padding(.horizontal, 10).padding(.vertical, 7).frame(maxWidth: .infinity, alignment: .leading)
                        .background(RoundedRectangle(cornerRadius: 8).fill(Color.primary.opacity(0.06)))
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.top, 6)
    }
}

struct Thinking: View {
    @ObservedObject var chat: ChatModel
    var body: some View {
        HStack(spacing: 8) {
            ProgressView().controlSize(.small).scaleEffect(0.8)
            Text(chat.progress).font(.system(size: 12)).foregroundColor(.secondary)
            Spacer()
            Button("Stop") { chat.stop() }.buttonStyle(.plain).font(.system(size: 12)).foregroundColor(.accentColor)
        }
        .padding(.vertical, 4)
    }
}

struct MessageView: View {
    @ObservedObject var model: AppModel
    @ObservedObject var chat: ChatModel
    let message: ChatMessage

    var body: some View {
        switch message.role {
        case .user:
            HStack {
                Spacer(minLength: 50)
                Text(message.text).font(.system(size: 13)).textSelection(.enabled)
                    .padding(.horizontal, 10).padding(.vertical, 7)
                    .background(RoundedRectangle(cornerRadius: 10).fill(Color.accentColor.opacity(0.16)))
            }
        case .assistant:
            VStack(alignment: .leading, spacing: 6) {
                Text(AskText.attributed(message.text, sessions: message.sessions))
                    .font(.system(size: 13)).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                    .environment(\.openURL, OpenURLAction { url in chat.open(url) ? .handled : .discarded })
                if !message.sessions.isEmpty {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(message.sessions) { r in SessionRow(model: model, row: r) }
                    }
                    .background(RoundedRectangle(cornerRadius: 8).fill(Color.primary.opacity(0.04)))
                }
                HStack(spacing: 8) {
                    if let meta = message.meta { Text(meta).font(.system(size: 10.5)).foregroundColor(.secondary.opacity(0.85)) }
                    IconButton(symbol: "doc.on.doc", help: "Copy the answer") { chat.copy(message) }
                    Spacer()
                }
            }
        case .error:
            HStack(alignment: .top, spacing: 6) {
                Image(systemName: message.code == "stopped" ? "stop.circle" : "exclamationmark.triangle")
                    .font(.system(size: 11.5)).foregroundColor(message.code == "stopped" ? .secondary : .orange)
                Text(message.text).font(.system(size: 12)).foregroundColor(message.code == "stopped" ? .secondary : .orange)
                    .fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
            }
        }
    }
}

struct Composer: View {
    @ObservedObject var chat: ChatModel
    var focused: FocusState<Bool>.Binding
    var close: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            TextField("Ask about your sessions…", text: $chat.draft)
                .textFieldStyle(.plain).font(.system(size: 13.5))
                .focused(focused)
                .onSubmit { chat.send() }
                .onExitCommand { if chat.draft.isEmpty { close() } else { chat.draft = "" } }
            if chat.thinking {
                Button { chat.stop() } label: { Image(systemName: "stop.circle.fill").font(.system(size: 20)).foregroundColor(.accentColor) }
                    .buttonStyle(.plain).help("Stop")
            } else {
                Button { chat.send() } label: {
                    Image(systemName: "arrow.up.circle.fill").font(.system(size: 20))
                        .foregroundColor(chat.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? .secondary.opacity(0.5) : .accentColor)
                }
                .buttonStyle(.plain).help("Send (Return)")
                .disabled(chat.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(.horizontal, 12).padding(.vertical, 9)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color.primary.opacity(0.07)))
        .padding(.horizontal, 12).padding(.vertical, 10)
    }
}
