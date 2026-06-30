export interface WhatsAppQrPayload {
  qrCode: string;
  status: "qr_required";
  generatedAt: string;
}

export function buildWhatsAppQrPayload(qrCode: string): WhatsAppQrPayload {
  return {
    qrCode: String(qrCode || "").trim(),
    status: "qr_required",
    generatedAt: new Date().toISOString(),
  };
}
