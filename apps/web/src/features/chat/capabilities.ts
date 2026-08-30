export type CapabilityStatus = {
  hospitalDataAvailable: boolean;
};

export async function loadCapabilityStatus(
  backendBaseUrl: string,
): Promise<CapabilityStatus> {
  try {
    const response = await fetch(`${backendBaseUrl}/v1/capabilities/status`);
    if (!response.ok) return { hospitalDataAvailable: false };
    const data = await response.json() as { hospital_data_available?: boolean };
    return { hospitalDataAvailable: data.hospital_data_available === true };
  } catch {
    return { hospitalDataAvailable: false };
  }
}
