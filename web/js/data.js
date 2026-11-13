const samples = {
  application_approval: {
    applicant: {
      name: "Ada Lovelace",
      email: "ada@example.com",
      credit_score: 720,
      monthly_income: 4000,
      employment_status: "employed",
    },
    requested_amount: 15000,
    country: "US",
    reference: "WEB-DEMO-A",
  },
  claim_processing: {
    policy_id: "POL-123456",
    claim_amount: 5000,
    claimant: { name: "Grace Hopper", email: "grace@example.com" },
    documents: [
      { type: "invoice", reference: "DOC-100" },
      { type: "photo", reference: "DOC-101" },
    ],
    incident_date: "2026-01-10",
  },
  employee_onboarding: {
    candidate: {
      name: "Alan Turing",
      email: "alan@example.com",
      role: "engineer",
      consent_background_check: true,
    },
    start_date: "2026-02-01",
    documents: [{ type: "passport", reference: "ID-900" }],
    equipment_requested: false,
  },
  vendor_approval: {
    vendor: {
      name: "Acme Supplies",
      tax_id: "ABC12345",
      country: "US",
      risk_rating: "low",
      annual_revenue: 5000000,
    },
    contract_value: 50000,
    cost_center: "OPS-1",
  },
  document_verification: {
    document: {
      type: "passport",
      number: "P1234567",
      expires_on: "2030-01-01",
      issuing_country: "US",
    },
    owner: { name: "Katherine Johnson", email: "kj@example.com" },
    file_ref: "s3://demo-bucket/doc.pdf",
  },
};

export function sampleInput(workflow) {
  const sample = samples[workflow];
  return sample ? JSON.parse(JSON.stringify(sample)) : { reference: "WEB-DEMO" };
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function stampDigits(stamp) {
  return String(Math.abs(stamp) % 1_000_000).padStart(6, "0");
}

export function demoRuns(stamp = Date.now()) {
  const digits = stampDigits(stamp);
  const base = clone(samples.application_approval);

  return [
    {
      workflow: "application_approval",
      input: {
        ...clone(base),
        reference: `WEB-${digits}-A`,
      },
    },
    {
      workflow: "application_approval",
      input: {
        ...clone(base),
        applicant: { ...base.applicant, name: "Grace Hopper", credit_score: 700, monthly_income: 5500 },
        requested_amount: 30000,
        reference: `WEB-${digits}-B`,
      },
    },
    {
      workflow: "application_approval",
      input: {
        ...clone(base),
        applicant: { ...base.applicant, name: "Katherine Johnson", credit_score: 640 },
        reference: `WEB-${digits}-C`,
      },
    },
    {
      workflow: "claim_processing",
      input: {
        ...clone(samples.claim_processing),
        policy_id: `POL-${digits}`,
        documents: [
          { type: "invoice", reference: `DOC-${digits}-1` },
          { type: "photo", reference: `DOC-${digits}-2` },
        ],
      },
    },
    {
      workflow: "vendor_approval",
      input: {
        ...clone(samples.vendor_approval),
        vendor: { ...samples.vendor_approval.vendor, name: "Northwind Components", tax_id: `NW${digits}` },
        contract_value: 75000,
      },
    },
    {
      workflow: "employee_onboarding",
      input: {
        ...clone(samples.employee_onboarding),
        documents: [{ type: "passport", reference: `EMP-${digits}` }],
      },
    },
    {
      workflow: "document_verification",
      input: {
        ...clone(samples.document_verification),
        document: { ...samples.document_verification.document, number: `P${digits}` },
      },
    },
    {
      workflow: "application_approval",
      input: {
        ...clone(base),
        applicant: { ...base.applicant, name: "Dorothy Vaughan" },
        reference: `WEB-${digits}-FAULT`,
      },
      simulation: { faults: { credit_bureau: "error" } },
    },
  ];
}
