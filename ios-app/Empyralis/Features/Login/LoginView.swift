import SwiftUI

struct LoginView: View {
    @EnvironmentObject private var session: SessionStore
    @Environment(\.colorScheme) private var scheme

    @State private var email = ""
    @State private var password = ""
    @State private var isLoading = false
    @State private var isGoogleLoading = false
    @FocusState private var focusedField: Field?

    private enum Field { case email, password }

    /// THE ACCENT FOLLOWS THE PRIMARY ACTION, WHICH IS NOT ALWAYS THE SAME
    /// CONTROL. With Google present it is the top pill and email/password is
    /// the fallback beneath it — Linear's own shape. With Google absent there
    /// is only one way in, and a screen whose single action is painted as a
    /// secondary has no primary action at all. One rule, computed, so the two
    /// arrangements can never both be accented and never both be neutral.
    private var googleIsPrimary: Bool { session.googleSignInAvailable }

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

                    // GOOGLE FIRST, THEN THE FORM — the founder's own
                    // reference: "logging in should go to a specific link to
                    // Google to do it because in Linear it's like that."
                    // Linear puts the provider above the fields because it is
                    // the faster door, not because it is the only one.
                    //
                    // THE WHOLE BLOCK IS ABSENT, NOT DISABLED, when this
                    // build has no Google client id or the backend has Google
                    // switched off (SessionStore.googleSignInAvailable). A
                    // greyed-out provider button is the dead control this
                    // codebase forbids, and an "or" rule dangling under
                    // nothing is worse.
                    if session.googleSignInAvailable {
                        Button {
                            signInWithGoogle()
                        } label: {
                            if isGoogleLoading {
                                ProgressView().tint(Theme.accentContrast)
                            } else {
                                HStack(spacing: Space.x2) {
                                    Image("GoogleMark")
                                        .resizable()
                                        .scaledToFit()
                                        .frame(width: 18, height: 18)
                                    Text("Continue with Google")
                                }
                            }
                        }
                        .buttonStyle(AuthPrimaryPillStyle())
                        .disabled(isGoogleLoading || isLoading)
                        .padding(.top, Space.x8)

                        orRule.padding(.top, Space.x5)
                    }

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
                    .padding(.top, googleIsPrimary ? Space.x5 : Space.x8)

                    if let error = session.lastErrorMessage {
                        Text(error)
                            .font(.empSecondary)
                            .foregroundStyle(Theme.offline(scheme))
                            .multilineTextAlignment(.center)
                            .padding(.top, Space.x4)
                    }

                    // Exactly one accent pill on this screen, whichever
                    // arrangement is showing — see `googleIsPrimary`.
                    Group {
                        if googleIsPrimary {
                            Button(action: submit) { continueLabel(tint: Theme.textPrimary(scheme)) }
                                .buttonStyle(AuthSecondaryPillStyle())
                        } else {
                            Button(action: submit) { continueLabel(tint: Theme.accentContrast) }
                                .buttonStyle(AuthPrimaryPillStyle())
                        }
                    }
                    .disabled(email.isEmpty || password.isEmpty || isLoading || isGoogleLoading)
                    .padding(.top, Space.x4)

                    Spacer(minLength: Space.x8)
                }
                .padding(.horizontal, Space.x5)
                .frame(maxWidth: .infinity, minHeight: proxy.size.height)
                }
                .scrollDismissesKeyboard(.interactively)
            }
        }
        // Confirms with the server that Google is actually on. Not awaited
        // before rendering — the button's first-frame state comes from this
        // build's own config, and this call can only ever take it away.
        .task { await session.refreshGoogleAvailability() }
    }

    @ViewBuilder
    private func continueLabel(tint: Color) -> some View {
        if isLoading {
            ProgressView().tint(tint)
        } else {
            Text("Continue")
        }
    }

    /// Says the two doors are alternatives rather than a sequence. A hairline
    /// either side of one lowercase word — the divider carries the meaning,
    /// so the word stays as quiet as it can be and still be read.
    private var orRule: some View {
        HStack(spacing: Space.x3) {
            line
            Text("or")
                .font(.empSecondary)
                .foregroundStyle(Theme.textMuted(scheme))
            line
        }
    }

    private var line: some View {
        Rectangle()
            .fill(Theme.border(scheme))
            .frame(height: 1)
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
        // minHeight, not fixed — same reasoning as AuthPrimaryPillStyle in
        // Components.swift, which this field is styled to match. The font
        // above is non-scaling .system(size:) today, so this cannot actually
        // overflow yet; minHeight is zero-cost hardening against that being
        // fixed later without this frame being revisited. See the audit
        // report's non-scaling-font finding.
        .frame(minHeight: 52)
        .background(Theme.bgField(scheme), in: Capsule())
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

    private func signInWithGoogle() {
        guard !isGoogleLoading, !isLoading else { return }
        focusedField = nil
        isGoogleLoading = true
        Task {
            await session.loginWithGoogle()
            isGoogleLoading = false
        }
    }
}
