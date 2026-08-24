import SwiftUI

struct LoginView: View {
    @EnvironmentObject private var session: SessionStore
    @Environment(\.colorScheme) private var scheme

    @State private var email = ""
    @State private var password = ""
    @State private var isLoading = false
    @FocusState private var focusedField: Field?

    private enum Field { case email, password }

    var body: some View {
        ZStack {
            Theme.bgPage(scheme).ignoresSafeArea()

            // GeometryReader + minHeight is what makes the Spacers actually
            // expand. A bare ScrollView sizes to its content, so Spacer()
            // collapses and the whole column pins to the top with dead space
            // beneath it — measured, and it read as unbalanced against
            // Linear's centred stack. The ScrollView is still required: with
            // the keyboard up on a small phone this content does not fit.
            GeometryReader { proxy in
                ScrollView {
                VStack(spacing: 0) {
                    // Generous, deliberately. The entry screens are the one
                    // place in this app that is airy rather than dense —
                    // there is a single decision here and nothing to scan.
                    Spacer(minLength: Space.x8)

                    BrandMark(size: 64)

                    Text("Log in to Empyralis")
                        .font(.system(size: 26, weight: .semibold))
                        .foregroundStyle(Theme.textPrimary(scheme))
                        .padding(.top, Space.x5)

                    VStack(spacing: Space.x3) {
                        pill("Email", text: $email, isSecure: false)
                            .focused($focusedField, equals: .email)
                            .textContentType(.username)
                            .keyboardType(.emailAddress)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .submitLabel(.next)
                            .onSubmit { focusedField = .password }

                        pill("Password", text: $password, isSecure: true)
                            .focused($focusedField, equals: .password)
                            .textContentType(.password)
                            .submitLabel(.go)
                            .onSubmit { submit() }
                    }
                    .padding(.top, Space.x8)

                    if let error = session.lastErrorMessage {
                        Text(error)
                            .font(.empSecondary)
                            .foregroundStyle(Theme.offline(scheme))
                            .multilineTextAlignment(.center)
                            .padding(.top, Space.x4)
                    }

                    Button {
                        submit()
                    } label: {
                        if isLoading {
                            ProgressView().tint(Theme.accentContrast)
                        } else {
                            Text("Continue")
                        }
                    }
                    .buttonStyle(AuthPrimaryPillStyle())
                    .disabled(email.isEmpty || password.isEmpty || isLoading)
                    .padding(.top, Space.x4)

                    Spacer(minLength: Space.x8)
                }
                .padding(.horizontal, Space.x5)
                .frame(maxWidth: .infinity, minHeight: proxy.size.height)
                }
                .scrollDismissesKeyboard(.interactively)
            }
        }
    }

    /// A text field wearing the same 52pt capsule as the buttons, so the
    /// column reads as one stack of equal-weight rows rather than a form
    /// with a button stapled underneath.
    @ViewBuilder
    private func pill(_ placeholder: String, text: Binding<String>, isSecure: Bool) -> some View {
        Group {
            if isSecure {
                SecureField(placeholder, text: text)
            } else {
                TextField(placeholder, text: text)
            }
        }
        .font(.system(size: 16))
        .foregroundStyle(Theme.textPrimary(scheme))
        .multilineTextAlignment(.center)
        .padding(.horizontal, Space.x5)
        .frame(height: 52)
        .background(
            scheme == .dark ? Color(hex: 0x1C1C1E) : Color(hex: 0xF2F2F4),
            in: Capsule()
        )
        .overlay(Capsule().stroke(Theme.border(scheme), lineWidth: 1))
    }

    private func submit() {
        guard !email.isEmpty, !password.isEmpty, !isLoading else { return }
        focusedField = nil
        isLoading = true
        Task {
            await session.login(email: email, password: password)
            isLoading = false
        }
    }
}
