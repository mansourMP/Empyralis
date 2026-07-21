// Reusable country-flag primitives for any cloud-region picker (DigitalOcean,
// Hetzner, Vultr, AWS, Google, and whatever provider comes next) — one place
// that knows "region id -> country" and "country -> flag", so every surface
// that lists regions (VPS setup wizard, Hardware list, future provider
// pickers) renders the same flag for the same region instead of each screen
// growing its own ad-hoc mapping.
//
// Adding a provider is exactly one more entry in REGION_COUNTRY below — no
// other code needs to change.

// Display name for every ISO 3166-1 alpha-2 code referenced by
// REGION_COUNTRY or CITY_COUNTRY below. Keep this in sync when either map
// grows a new country.
export const COUNTRY_NAME: Record<string, string> = {
  US: 'United States',
  NL: 'Netherlands',
  SG: 'Singapore',
  GB: 'United Kingdom',
  DE: 'Germany',
  CA: 'Canada',
  IN: 'India',
  AU: 'Australia',
  FI: 'Finland',
  FR: 'France',
  SE: 'Sweden',
  JP: 'Japan',
  IE: 'Ireland',
  BR: 'Brazil',
  BE: 'Belgium',
  ES: 'Spain',
  NO: 'Norway',
  KR: 'South Korea',
  IL: 'Israel',
  PL: 'Poland',
  ZA: 'South Africa',
};

// Returns the regional-indicator-symbol flag emoji for a 2-letter ISO
// country code (e.g. "US" -> the US flag). Each letter A-Z maps to a
// Unicode regional indicator symbol starting at U+1F1E6; a flag emoji is
// just the pair of regional indicators for the two letters placed next to
// each other. Returns "" for anything that isn't exactly two A-Z letters —
// callers must never render a placeholder/wrong flag for bad input.
export function countryFlagEmoji(iso2: string): string {
  const code = (iso2 || '').trim().toUpperCase();
  if (!/^[A-Z]{2}$/.test(code)) {
    return '';
  }
  const points = Array.from(code, (letter) => 0x1f1e6 + (letter.charCodeAt(0) - 65));
  return String.fromCodePoint(...points);
}

type CountryFlagProps = {
  code: string;
  size?: number;
};

// Small inline flag. Emoji is the zero-asset choice today — no image
// requests, no sprite sheet, and macOS/iOS (Mansur's dev + demo devices)
// render regional-indicator emoji as real full-color country flags out of
// the box. Upgrade path: if we ever need flags to render identically on
// platforms that show letter-pair text instead of a flag (older
// Windows/Linux), swap the <span> body below for a self-hosted SVG
// (e.g. `/flags/{code}.svg`) — every call site stays `<CountryFlag code=.../>`
// unchanged since the code/size props don't need to change.
export function CountryFlag({ code, size }: CountryFlagProps) {
  const emoji = countryFlagEmoji(code);
  if (!emoji) {
    return null;
  }
  const normalized = (code || '').trim().toUpperCase();
  const label = COUNTRY_NAME[normalized] || normalized;
  return (
    <span
      role="img"
      aria-label={label}
      style={{
        display: 'inline-block',
        lineHeight: 1,
        fontSize: size ? `${size}px` : undefined,
      }}
    >
      {emoji}
    </span>
  );
}

// providerId -> regionId -> ISO2 country. Add a provider by adding one more
// top-level key; add a region by adding one more entry inside it.
export const REGION_COUNTRY: Record<string, Record<string, string>> = {
  digitalocean: {
    nyc1: 'US',
    nyc2: 'US',
    nyc3: 'US',
    sfo1: 'US',
    sfo2: 'US',
    sfo3: 'US',
    ams2: 'NL',
    ams3: 'NL',
    sgp1: 'SG',
    lon1: 'GB',
    fra1: 'DE',
    tor1: 'CA',
    blr1: 'IN',
    syd1: 'AU',
  },
  hetzner: {
    fsn1: 'DE',
    nbg1: 'DE',
    hel1: 'FI',
    ash: 'US',
    hil: 'US',
  },
  vultr: {
    ewr: 'US',
    lax: 'US',
    ord: 'US',
    dfw: 'US',
    sea: 'US',
    atl: 'US',
    mia: 'US',
    sjc: 'US',
    hnl: 'US',
    ams: 'NL',
    lhr: 'GB',
    man: 'GB',
    fra: 'DE',
    par: 'FR',
    sto: 'SE',
    sgp: 'SG',
    nrt: 'JP',
    itm: 'JP',
    syd: 'AU',
    mel: 'AU',
    blr: 'IN',
    bom: 'IN',
    del: 'IN',
    icn: 'KR',
    mad: 'ES',
    osl: 'NO',
    sao: 'BR',
    tlv: 'IL',
    waw: 'PL',
    yto: 'CA',
    jnb: 'ZA',
  },
  aws: {
    'us-east-1': 'US',
    'us-east-2': 'US',
    'us-west-1': 'US',
    'us-west-2': 'US',
    'eu-west-1': 'IE',
    'eu-west-2': 'GB',
    'eu-central-1': 'DE',
    'ap-southeast-1': 'SG',
    'ap-southeast-2': 'AU',
    'ap-south-1': 'IN',
    'ap-northeast-1': 'JP',
    'ca-central-1': 'CA',
    'sa-east-1': 'BR',
  },
  google: {
    'us-central1': 'US',
    'us-east1': 'US',
    'us-west1': 'US',
    'europe-west1': 'BE',
    'europe-west2': 'GB',
    'europe-west3': 'DE',
    'asia-southeast1': 'SG',
    'asia-south1': 'IN',
    'asia-northeast1': 'JP',
    'australia-southeast1': 'AU',
    'northamerica-northeast1': 'CA',
    'southamerica-east1': 'BR',
  },
};

// google/gcp is the same provider id CLOUD_VPS_PROVIDERS already uses
// ('google'), but keep a 'gcp' alias so callers that key off the more
// common short name still resolve.
REGION_COUNTRY.gcp = REGION_COUNTRY.google;

// Best-effort city/country-name fallback for when a region id isn't in
// REGION_COUNTRY yet (new/unmapped provider region) but its human label
// mentions a recognizable place. Matched as a case-insensitive substring
// against the label, so "Frankfurt (fra1)" or "Amsterdam 3" both resolve.
const CITY_COUNTRY: Record<string, string> = {
  'new york': 'US',
  'san francisco': 'US',
  'silicon valley': 'US',
  chicago: 'US',
  dallas: 'US',
  seattle: 'US',
  atlanta: 'US',
  miami: 'US',
  ashburn: 'US',
  hillsboro: 'US',
  'los angeles': 'US',
  honolulu: 'US',
  'united states': 'US',
  amsterdam: 'NL',
  netherlands: 'NL',
  singapore: 'SG',
  london: 'GB',
  manchester: 'GB',
  'united kingdom': 'GB',
  frankfurt: 'DE',
  germany: 'DE',
  toronto: 'CA',
  canada: 'CA',
  bangalore: 'IN',
  bengaluru: 'IN',
  mumbai: 'IN',
  delhi: 'IN',
  india: 'IN',
  sydney: 'AU',
  melbourne: 'AU',
  australia: 'AU',
  helsinki: 'FI',
  finland: 'FI',
  paris: 'FR',
  france: 'FR',
  stockholm: 'SE',
  sweden: 'SE',
  tokyo: 'JP',
  osaka: 'JP',
  japan: 'JP',
  dublin: 'IE',
  ireland: 'IE',
  'sao paulo': 'BR',
  brazil: 'BR',
  brussels: 'BE',
  belgium: 'BE',
  madrid: 'ES',
  spain: 'ES',
  oslo: 'NO',
  norway: 'NO',
  seoul: 'KR',
  'south korea': 'KR',
  'tel aviv': 'IL',
  israel: 'IL',
  warsaw: 'PL',
  poland: 'PL',
  johannesburg: 'ZA',
  'south africa': 'ZA',
};

// Looks up REGION_COUNTRY[providerId][regionId] first; if that provider or
// region isn't mapped yet, falls back to scanning `label` for a known
// city/country name. Returns "" — never a guess — when nothing resolves, so
// callers can render no flag rather than a wrong one.
export function resolveRegionCountry(
  providerId: string | null | undefined,
  regionId: string,
  label?: string,
): string {
  if (providerId) {
    const country = REGION_COUNTRY[providerId]?.[regionId];
    if (country) {
      return country;
    }
  }
  const haystack = (label || '').toLowerCase();
  if (!haystack) {
    return '';
  }
  for (const [needle, iso2] of Object.entries(CITY_COUNTRY)) {
    if (haystack.includes(needle)) {
      return iso2;
    }
  }
  return '';
}
