// Phase UC: Fleet page renders in its own layout.
// The FleetHome component has its own full-viewport layout (rail + content + detail).
// This layout simply passes children through without the workstation shell.
export default function FleetLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
