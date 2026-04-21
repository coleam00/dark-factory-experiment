export function BrandingHeader() {
  return (
    <div className="flex flex-col items-center mb-4">
      <img src="/logo.svg" alt="DynaChat logo" className="w-10 h-10 mb-2" />
      <span className="text-xl font-semibold text-[var(--text-primary)]">DynaChat</span>
      <span className="text-sm text-[var(--text-secondary)]">
        Ask Cole Medin&apos;s YouTube library anything
      </span>
    </div>
  );
}
