type ProviderIconProps = {
  className?: string;
};

export function GoogleProviderIcon({ className }: ProviderIconProps) {
  return (
    <svg
      aria-hidden="true"
      className={className}
      viewBox="0 0 18 18"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      <path
        d="M16.954 9.205c0-.622-.056-1.218-.16-1.79H9v3.386h4.458a3.81 3.81 0 0 1-1.652 2.5v2.076h2.67c1.562-1.438 2.478-3.557 2.478-6.172Z"
        fill="#4285F4"
      />
      <path
        d="M9 17.25c2.227 0 4.093-.739 5.458-2.002l-2.67-2.076c-.739.496-1.685.79-2.788.79-2.14 0-3.952-1.446-4.6-3.39H1.64v2.142A8.248 8.248 0 0 0 9 17.25Z"
        fill="#34A853"
      />
      <path
        d="M4.4 10.572A4.952 4.952 0 0 1 4.144 9c0-.546.094-1.076.256-1.572V5.286H1.64A8.248 8.248 0 0 0 .75 9c0 1.325.318 2.58.89 3.714L4.4 10.572Z"
        fill="#FBBC05"
      />
      <path
        d="M9 4.038c1.211 0 2.298.417 3.154 1.235l2.368-2.368C13.089 1.579 11.226.75 9 .75a8.248 8.248 0 0 0-7.36 4.536L4.4 7.428c.648-1.944 2.46-3.39 4.6-3.39Z"
        fill="#EA4335"
      />
    </svg>
  );
}

