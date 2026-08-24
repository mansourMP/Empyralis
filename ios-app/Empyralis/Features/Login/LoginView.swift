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

            VStack(spacing: Space.x6) {
                Spacer()

                VStack(spacing: Space.x2) {
                    Text("Empyralis")
                        .font(.system(size: 32, weight: .semibold))
                        .foregroundStyle(Theme.textPrimary(scheme))
                    Text("See what your agents are doing.")
                        .font(.empSecondary)
                        .foregroundStyle(Theme.textMuted(scheme))
                }

                VStack(spacing: Space.x2) {
                    field("Email", text: $email, isSecure: false)
                        .focused($focusedField, equals: .email)
                        .textContentType(.username)
                        .keyboardType(.emailAddress)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .submitLabel(.next)
                        .onSubmit { focusedField = .password }

                    field("Password", text: $password, isSecure: true)
                        .focused($focusedField, equals: .password)
                        .textContentType(.password)
                        .submitLabel(.go)
                        .onSubmit { submit() }
                }

                if let error = session.lastErrorMessage {
                    Text(error)
                        .font(.empSecondary)
                        .foregroundStyle(Theme.offline(scheme))
                        .multilineTextAlignment(.center)
                }

                // The one accent-filled control in this view — and the only
                // primary action on screen.
                Button {
                    submit()
                } label: {
                    if isLoading {
                        ProgressView().tint(Theme.accentContrast)
                    } else {
                        Text("Sign In")
                    }
                }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(email.isEmpty || password.isEmpty || isLoading)

                Spacer()
                Spacer()
            }
            .padding(.horizontal, Space.x5)
        }
    }

    @ViewBuilder
    private func field(_ placeholder: String, text: Binding<String>, isSecure: Bool) -> some View {
        Group {
            if isSecure {
                SecureField(placeholder, text: text)
            } else {
                TextField(placeholder, text: text)
            }
        }
        .font(.empBody)
        .foregroundStyle(Theme.textPrimary(scheme))
        .padding(.horizontal, Space.x3)
        .frame(height: 44)
        .background(Theme.bgInset(scheme), in: RoundedRectangle(cornerRadius: Radius.control))
        .overlay(
            RoundedRectangle(cornerRadius: Radius.control)
                .stroke(Theme.border(scheme), lineWidth: 1)
        )
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
