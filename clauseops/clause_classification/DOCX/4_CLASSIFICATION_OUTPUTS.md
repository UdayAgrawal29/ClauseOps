# ClauseOps Phase 2: Classification Output Report

Generated on: 2026-05-31 15:19:39

This document demonstrates the full pipeline: **Raw PDF -> ML Segmentation -> Pre-filters -> Contracts-BERT Classification**.

Pre-classification filters applied:
- **Preamble/Recitals filter**: Segments with PREAMBLE/RECITALS/WHEREAS headings/body -> tagged `PREAMBLE`, skipped
- **Signature block filter**: Segments with <20 tokens and no legal verbs -> tagged `SIGNATURE_BLOCK`, skipped

---

## Document: Franchise Agreement
**Source:** `PfHospitalityGroupInc_20150923_10-12G_EX-10.1_9266710_EX-10.1_Franchise Agreement3.pdf`

**Stats:** 6 segments | Seg: 18.0s | Class: 2.5s | Filtered: 0 | Classified: 6 (High: 4, Review: 2)

#### Segment 1: APPENDIX C — SAMPLE OF NON-DISCLOSURE AND NON-COMPETITION AGREEMENT (BETWEEN FRANCHISEE AND ITS PERSONNEL)

| Property | Value |
|---|---|
| **Predicted Class** | `CONFIDENTIALITY` |
| **Confidence** | HIGH **99.1%** |
| Tokens | 248 |
| Source | direct |

**Body Text:**

> THIS SAMPLE OF NON-DISCLOSURE AND NON-COMPETITION AGREEMENT ( ' Agreement ') is made this _____ day of _________, 20___, by and between ___________________________ (the ' Franchisee '), and ___________________________, who is an officer, director, or employee of Franchisee (the ' Member '). RECITALS: WHEREAS , __________________ (' Franchisor ') has developed a distinctive set of specifications and operating procedures (collectively, the ' System ') for the operation of 'Pizza Fusion' restaurant... [truncated]

---

#### Segment 2: 1. Confidential Information.

| Property | Value |
|---|---|
| **Predicted Class** | `CONFIDENTIALITY` |
| **Confidence** | MEDIUM **49.9%** |
| **Alternatives** | CONFIDENTIALITY (49.9%), RENEWAL (48.3%), ENTIRE_AGREEMENT (1.0%) |
| Tokens | 672 |
| Source | sub_chunk_average(2) |
| Oversized | Yes |

**Body Text:**

> Member shall not, during the term of the Franchise Agreement or thereafter, communicate, divulge or use, for any purpose other than the operation of the Franchised Business, any confidential information, knowledge, trade secrets or know-how which may be communicated to Member or which Member may learn by virtue of Member's relationship with Franchisee. All information, knowledge and know-how relating to Franchisor, its business plans, Franchised Businesses, or the System (' Confidential Informat... [truncated]

---

#### Segment 3: 3.  Injunctive Relief.

| Property | Value |
|---|---|
| **Predicted Class** | `DISPUTE_RESOLUTION` |
| **Confidence** | HIGH **99.4%** |
| Tokens | 67 |
| Source | direct |

**Body Text:**

> Member acknowledges that any failure to comply with the requirements of this Agreement will cause Franchisor irreparable injury, and Member agrees to pay all court costs and reasonable attorney's fees incurred by Franchisor in obtaining specific performance of, or an injunction against violation of, the requirements of this Agreement.

---

#### Segment 4: 4. Severability.

| Property | Value |
|---|---|
| **Predicted Class** | `ENTIRE_AGREEMENT` |
| **Confidence** | HIGH **99.6%** |
| Tokens | 135 |
| Source | direct |

**Body Text:**

> All agreements and covenants contained herein are severable. If any of them, or any part or parts of them, shall be held invalid by any court of competent jurisdiction for any reason, then the Member agrees that the court shall have the authority to reform and modify that provision in order that the restriction shall be the maximum necessary to protect Franchisor's and/or Franchisee's legitimate business needs as permitted by applicable law and public policy. In so doing, the Member agrees that ... [truncated]

---

#### Segment 5: 5. Delay.

| Property | Value |
|---|---|
| **Predicted Class** | `ENTIRE_AGREEMENT` |
| **Confidence** | HIGH **99.6%** |
| Tokens | 94 |
| Source | direct |

**Body Text:**

> No delay or failure by the Franchisor or Franchisee to exercise any right under this Agreement, and no partial or single exercise of that right, shall constitute a waiver of that or any other right provided herein, and no waiver of any violation of any terms and provisions of this Agreement shall be construed as a waiver of any succeeding violation of the same or any other provision of this Agreement.

---

#### Segment 6: 6.  Third-Party Beneficiary.

| Property | Value |
|---|---|
| **Predicted Class** | `ENTIRE_AGREEMENT` |
| **Confidence** | MEDIUM **58.2%** |
| **Alternatives** | ENTIRE_AGREEMENT (58.2%), ASSIGNMENT (33.7%), WARRANTIES (2.9%) |
| Tokens | 81 |
| Source | direct |

**Body Text:**

> Member hereby acknowledges and agrees that Franchisor is an intended third-party beneficiary of this Agreement with the right to enforce it, independently or jointly with Franchisee. IN WITNESS WHEREOF , the Franchisee and the Member attest that each has read and understands the terms of this Agreement, and voluntarily signed this Agreement on the date first written above. FRANCHISEE MEMBER

---

## Document: Affiliate Agreement
**Source:** `LinkPlusCorp_20050802_8-K_EX-10_3240252_EX-10_Affiliate Agreement.pdf`

**Stats:** 9 segments | Seg: 7.1s | Class: 3.2s | Filtered: 0 | Classified: 9 (High: 4, Review: 5)

#### Segment 1: AFFILIATE AGREEMENT

| Property | Value |
|---|---|
| **Predicted Class** | `ENTIRE_AGREEMENT` |
| **Confidence** | MEDIUM **53.1%** |
| **Alternatives** | ENTIRE_AGREEMENT (53.1%), LIABILITY_LIMITATION (12.4%), ASSIGNMENT (9.2%) |
| Tokens | 215 |
| Source | direct |

**Body Text:**

> This Agreement entered into as of the Effective Date by and between Link Plus Corporation and Axiometric, LLC. RECITALS WHEREAS, Axiometric has developed certain computer software including wireless mesh networking technology and AMR devices and systems; WHEREAS, LKPL has developed certain radio devices and systems along with hardware manufacturing capacities and plans to develop AMR devices and systems; WHEREAS, LKPL and Axiometric believe it will be in their mutual best interests to cooperate ... [truncated]

---

#### Segment 2: 1.       DEFINITIONS

| Property | Value |
|---|---|
| **Predicted Class** | `DEFINITIONS` |
| **Confidence** | HIGH **99.5%** |
| Tokens | 68 |
| Source | direct |

**Body Text:**

> 1.1      Throughout this Agreement, and unless the context otherwise requires, the terms shown on Exhibit A (whether or not capitalized) shall have the meanings there specified. If other terms are defined in the text of this Agreement, then throughout this Agreement, those terms shall have the meanings respectively ascribed to them.

---

#### Segment 3: 2.       OFFICE SPACE

| Property | Value |
|---|---|
| **Predicted Class** | `ENTIRE_AGREEMENT` |
| **Confidence** | MEDIUM **46.6%** |
| **Alternatives** | ENTIRE_AGREEMENT (46.6%), DELIVERY_OBLIGATIONS (16.3%), INDEMNIFICATION (13.0%) |
| Tokens | 531 |
| Source | sub_chunk_average(2) |
| Oversized | Yes |

**Body Text:**

> 2.1      During the term of this Agreement, LKPL will provide Axiometric with a license to use office space in LKPL's corporate facility in Columbia, Maryland, free of charge. 2.2      LKPL will allow Axiometric to use enough office space for two individuals and associated equipment in locations convenient for LKPL's purposes for as long as that space is available and not needed by LKPL for its own purposes. Axiometric will have access to LKPL's telephone system, internet connections, conference... [truncated]

---

#### Segment 4: 3.       AUTOMATIC METER READING

| Property | Value |
|---|---|
| **Predicted Class** | `ENTIRE_AGREEMENT` |
| **Confidence** | MEDIUM **46.4%** |
| **Alternatives** | ENTIRE_AGREEMENT (46.4%), DELIVERY_OBLIGATIONS (36.0%), PAYMENT (8.0%) |
| Tokens | 74 |
| Source | direct |

**Body Text:**

> Axiometric and LKPL agree to jointly pursue accessing and commercially penetrating the AMR market by developing a suite of qualified and commercially marketable product suites for that market, marketing and selling that suite of products. The following shall be the general roles and responsibilities of the two companies with respect to AMR efforts:

---

#### Segment 5: 3.1      AMR Products

| Property | Value |
|---|---|
| **Predicted Class** | `DELIVERY_OBLIGATIONS` |
| **Confidence** | LOW **29.2%** |
| **Alternatives** | DELIVERY_OBLIGATIONS (29.2%), PAYMENT (24.2%), REPORTING_AUDIT (20.5%) |
| Tokens | 1779 |
| Source | sub_chunk_average(4) |
| Oversized | Yes |

**Body Text:**

> 3.1.1    Water Meter Development: Axiometric and LKPL are jointly developing an AMR product for remote reading of residential water utility meters (hereafter the Water Meter). The Water Meter is a hardware device with integral software. The software includes, under license, the Axiometric wireless mesh networking intellectual property. The Water Meter is capable of interfacing with a variety of water meter registers, recording water usage, logging various exceptional conditions, and reporting th... [truncated]

---

#### Segment 6: 4.       RELATIONSHIP OF THE PARTIES

| Property | Value |
|---|---|
| **Predicted Class** | `RENEWAL` |
| **Confidence** | HIGH **88.1%** |
| Tokens | 191 |
| Source | direct |

**Body Text:**

> 4.1      The parties will be joint venturers only as to those activities that they jointly undertake for the AMR market as described in section 3 above; otherwise they shall be independent of each other, with full control over their respective activities without the need to account to the other, and independent contractors as to all work performed under separate agreements. Even though the parties will be joint ventureres as to the AMR market, neither party will have the right to bind the other ... [truncated]

---

#### Segment 7: 6.       NOTICES

| Property | Value |
|---|---|
| **Predicted Class** | `NOTICES` |
| **Confidence** | HIGH **99.8%** |
| Tokens | 83 |
| Source | direct |

**Body Text:**

> All notices and communications required or permitted to be given under this Agreement will be deemed given after receipt when sent by United States Postal Service as registered or certified mail, postage prepaid, and addressed to the other party at the notice addresses set forth on the signature page (unless by such notice a different person or address shall have been designated)

---

#### Segment 8: ADDITIONAL PROVISIONS.

| Property | Value |
|---|---|
| **Predicted Class** | `GOVERNING_LAW` |
| **Confidence** | MEDIUM **65.2%** |
| **Alternatives** | GOVERNING_LAW (65.2%), ENTIRE_AGREEMENT (25.0%), DISPUTE_RESOLUTION (3.9%) |
| Tokens | 393 |
| Source | direct |

**Body Text:**

> 7.1      This Agreement may not be assigned in whole or in part by either party without prior written consent of the other. 7.2      All actions, cases, suits and proceedings in connection with this Agreement shall be brought in Maryland. All persons affected by this Agreement specifically consent to the personal jurisdiction of and venue in said courts. No action, case, suit or proceeding, regardless of form, arising out of or related to this Agreement, may be brought by either party more than ... [truncated]

---

#### Segment 9: LINK PLUS CORPORATION                       AXIOMETRIC, LLC

| Property | Value |
|---|---|
| **Predicted Class** | `DEFINITIONS` |
| **Confidence** | HIGH **80.3%** |
| Tokens | 357 |
| Source | direct |

**Body Text:**

> By:                                         By: ---------------------------                 -------------------------------- Robert L. Jones, Jr.                          Frank Moody Chairman, CEO                                 Managing Director Notice Addresses: Link Plus Corporation 6996 Columbia Gateway Drive, Suite 104 Columbia, MD 21046 Attention: Chief Operating Officer Axiometric, LLC 10718 Vista Road Columbia, MD 21044 EXHIBIT A - SELECTED DEFINITIONS "Axiometric" means Axiometric, LLC.... [truncated]

---

## Document: Co-Branding Agreement
**Source:** `PcquoteComInc_19990721_S-1A_EX-10.11_6377149_EX-10.11_Co-Branding Agreement3.pdf`

**Stats:** 2 segments | Seg: 1.8s | Class: 0.4s | Filtered: 1 | Classified: 1 (High: 1, Review: 0)

#### Segment 1: PREAMBLE

| Property | Value |
|---|---|
| **Predicted Class** | `PREAMBLE` |
| **Confidence** | FILTERED **100.0%** |
| Tokens | 2 |
| Source | filtered |

**Body Text:**

> [LOGO]

---

#### Segment 2: SECOND AMENDMENT TO CO-BRANDING AGREEMENT

| Property | Value |
|---|---|
| **Predicted Class** | `ENTIRE_AGREEMENT` |
| **Confidence** | HIGH **98.8%** |
| Tokens | 448 |
| Source | direct |

**Body Text:**

> THIS SECOND AMENDMENT TO CO-BRANDING AGREEMENT (this "Amendment") is made and entered into, effective for all purposes and in all respects as of the 23rd day of February, 1998, by and between PC QUOTE, INC., with its principal place of business at 300 South Wacker Drive, Chicago, Illinois 60605 ("PCQ") and A.B. Watley, Inc., with its principal place of business at 33 West 17th Street, New York, New York 10011 ("ABW"). WHEREAS, PCQ and ABW have executed that certain Co-Branding Agreement dated Oc... [truncated]

---

## Document: Marketing Agreement
**Source:** `EmmisCommunicationsCorp_20191125_8-K_EX-10.6_11906433_EX-10.6_Marketing Agreement.pdf`

**Stats:** 14 segments | Seg: 7.0s | Class: 2.5s | Filtered: 3 | Classified: 11 (High: 5, Review: 6)

#### Segment 1: LOCAL PROGRAMMING AND MARKETING AGREEMENT

| Property | Value |
|---|---|
| **Predicted Class** | `ENTIRE_AGREEMENT` |
| **Confidence** | MEDIUM **69.8%** |
| **Alternatives** | ENTIRE_AGREEMENT (69.8%), LIABILITY_LIMITATION (9.0%), INDEMNIFICATION (6.6%) |
| Tokens | 54 |
| Source | direct |

**Body Text:**

> (WQHT HD2) THIS LOCAL PROGRAMMING AND MARKETING AGREEMENT (this 'Agreement') is made as of November 25, 2019 by and between MediaCo Holding Inc., an Indiana corporation (the 'Licensee'), and WBLS-WLIB LLC, an Indiana limited liability company ('Programmer').

---

#### Segment 2: Recitals

| Property | Value |
|---|---|
| **Predicted Class** | `PREAMBLE` |
| **Confidence** | FILTERED **100.0%** |
| Tokens | 135 |
| Source | filtered |

**Body Text:**

> Licensee owns and operates the following radio station (the 'Station') pursuant to licenses issued by the Federal Communications Commission ('FCC'): WQHT-FM, New York, NY (Facility ID No. 19615). The Station has the capability to transmit an in-band, on-channel ('IBOC') digital broadcast signal. Programmer desires to have radio broadcast station WLIB-AM, New York, NY (Facility ID No. 28204) ('WLIB') rebroadcast on the Station's HD-2 channel (the 'HD2 Channel') at a bandwidth of 24kbps. Licensee ... [truncated]

---

#### Segment 3: Agreement

| Property | Value |
|---|---|
| **Predicted Class** | `RENEWAL` |
| **Confidence** | LOW **34.5%** |
| **Alternatives** | RENEWAL (34.5%), WARRANTIES (28.5%), ENTIRE_AGREEMENT (24.4%) |
| Tokens | 1027 |
| Source | sub_chunk_average(3) |
| Oversized | Yes |

**Body Text:**

> NOW, THEREFORE, taking the foregoing recitals into account, and in consideration of the mutual covenants and agreements contained herein and for other good and valuable consideration, the receipt and sufficiency of which are hereby acknowledged, the parties, intending to be legally bound, agree as follows: Agreement Term. The term of this Agreement (the 'Term') will begin on the date hereof (the 'Commencement Date'), and will continue until the earlier of (i) December 31, 2022, (ii) the terminat... [truncated]

---

#### Segment 4: 8.1 Production of the Programs.

| Property | Value |
|---|---|
| **Predicted Class** | `ENTIRE_AGREEMENT` |
| **Confidence** | MEDIUM **63.1%** |
| **Alternatives** | ENTIRE_AGREEMENT (63.1%), DEFINITIONS (18.6%), WARRANTIES (8.0%) |
| Tokens | 58 |
| Source | direct |

**Body Text:**

> Programmer agrees that the contents of the WLIB Programs it transmits to Licensee shall conform to all FCC rules, regulations and policies. Programmer shall provide only the WLIB Programs, and not any other programming, for broadcast on the HD2 Channel.

---

#### Segment 5: 8.2 Political Time.

| Property | Value |
|---|---|
| **Predicted Class** | `ENTIRE_AGREEMENT` |
| **Confidence** | MEDIUM **53.3%** |
| **Alternatives** | ENTIRE_AGREEMENT (53.3%), DELIVERY_OBLIGATIONS (26.4%), WARRANTIES (5.7%) |
| Tokens | 358 |
| Source | direct |

**Body Text:**

> Licensee shall oversee and take ultimate responsibility with respect to the provision of equal opportunities, lowest unit charge, and reasonable access to political candidates, and compliance with the political broadcast rules of the FCC. During the Term, Programmer shall cooperate with Licensee as Licensee complies with its political broadcast responsibilities, and shall supply such information promptly to Licensee as may be necessary to comply with the political advertising time record keeping... [truncated]

---

#### Segment 6: 11.1 Programmer's Events of Default.

| Property | Value |
|---|---|
| **Predicted Class** | `DISPUTE_RESOLUTION` |
| **Confidence** | HIGH **93.8%** |
| Tokens | 76 |
| Source | direct |

**Body Text:**

> The occurrence of any of the following will be deemed an Event of Default by Programmer under this Agreement: (a) Programmer fails to observe or perform its obligations contained in this Agreement in any material respect; or (b) Programmer breaches the representations and warranties made by it under this Agreement in any material respect.

---

#### Segment 7: 11.2 Licensee Events of Default.

| Property | Value |
|---|---|
| **Predicted Class** | `DISPUTE_RESOLUTION` |
| **Confidence** | HIGH **87.5%** |
| Tokens | 74 |
| Source | direct |

**Body Text:**

> The occurrence of the following will be deemed an Event of Default by Licensee under this Agreement: (a) Licensee fails to observe or perform its obligations contained in this Agreement in any material respect; or (b) Licensee breaches the representations and warranties made by it under this Agreement in any material respect.

---

#### Segment 8: 11.3 Cure Period.

| Property | Value |
|---|---|
| **Predicted Class** | `NOTICES` |
| **Confidence** | MEDIUM **59.1%** |
| **Alternatives** | NOTICES (59.1%), ENTIRE_AGREEMENT (20.4%), PAYMENT (9.9%) |
| Tokens | 58 |
| Source | direct |

**Body Text:**

> Notwithstanding the foregoing, any Event of Default will not be deemed to have occurred until fifteen (15) days after the non-defaulting party has provided the defaulting party with written notice specifying the Event of Default and such Event of Default remains uncured.

---

#### Segment 9: 11.4 Termination in the Event of Default.

| Property | Value |
|---|---|
| **Predicted Class** | `TERMINATION` |
| **Confidence** | HIGH **99.6%** |
| Tokens | 55 |
| Source | direct |

**Body Text:**

> Upon the occurrence of an Event of Default, and in the absence of a timely cure pursuant to Section 11.3, the non-defaulting party may terminate this Agreement, effective immediately upon written notice to the defaulting party.

---

#### Segment 10: 11.5 Cooperation Upon Termination.

| Property | Value |
|---|---|
| **Predicted Class** | `ENTIRE_AGREEMENT` |
| **Confidence** | MEDIUM **50.0%** |
| **Alternatives** | ENTIRE_AGREEMENT (50.0%), WARRANTIES (30.0%), INDEMNIFICATION (16.4%) |
| Tokens | 695 |
| Source | sub_chunk_average(2) |
| Oversized | Yes |

**Body Text:**

> If this Agreement is terminated for any reason, the parties agree to cooperate with one another and to take all actions necessary to rescind this Agreement and return the parties to the status quo ante. Indemnification. Programmer shall indemnify and hold Licensee harmless against any and all liability arising from Programmer's use of Licensee's facilities, if any, or from the broadcast of the WLIB Programs on the HD2 Channel, including without limitation for libel, slander, illegal competition ... [truncated]

---

#### Segment 11: 18. Notices.

| Property | Value |
|---|---|
| **Predicted Class** | `NOTICES` |
| **Confidence** | HIGH **99.8%** |
| Tokens | 85 |
| Source | direct |

**Body Text:**

> Any notice pursuant to this Agreement shall be in writing and shall be deemed delivered on the date of personal delivery or confirmed delivery by a nationally-recognized overnight courier service, or on the third day after prepaid mailing by certified U.S. mail, return receipt requested, and shall be addressed as follows (or to such other address as any party may request by written notice):

---

#### Segment 12: 19. Entire Agreement.

| Property | Value |
|---|---|
| **Predicted Class** | `ENTIRE_AGREEMENT` |
| **Confidence** | HIGH **99.0%** |
| Tokens | 499 |
| Source | sub_chunk_average(2) |
| Oversized | Yes |

**Body Text:**

> This Agreement embodies the entire agreement, and supersedes all prior oral or written understandings, between the parties with respect to the subject matter of this Agreement. Relationship of Parties. Neither the Programmer nor Licensee will be deemed to be the agent, partner, or representative of the other party to this Agreement, and neither party is authorized to bind the other to any contract, agreement, or understanding. Force Majeure and Facilities Upgrades. The failure of either party he... [truncated]

---

#### Segment 13: J. Scott Enright

| Property | Value |
|---|---|
| **Predicted Class** | `SIGNATURE_BLOCK` |
| **Confidence** | FILTERED **100.0%** |
| Tokens | 14 |
| Source | filtered |

**Body Text:**

> Title: Executive Vice President, General Counsel & Secretary

---

#### Segment 14: J. Scott Enright

| Property | Value |
|---|---|
| **Predicted Class** | `SIGNATURE_BLOCK` |
| **Confidence** | FILTERED **100.0%** |
| Tokens | 13 |
| Source | filtered |

**Body Text:**

> Executive Vice President, General Counsel & Secretary

---

## Document: Development Agreement
**Source:** `EmeraldHealthBioceuticalsInc_20200218_1-A_EX1A-6 MAT CTRCT_11987205_EX1A-6 MAT CTRCT_Development Agreement.pdf`

**Stats:** 19 segments | Seg: 6.2s | Class: 1.7s | Filtered: 1 | Classified: 18 (High: 16, Review: 2)

#### Segment 1: ARTICLE 1 -- PREAMBLE

| Property | Value |
|---|---|
| **Predicted Class** | `PREAMBLE` |
| **Confidence** | FILTERED **100.0%** |
| Tokens | 89 |
| Source | filtered |

**Body Text:**

> This Consulting and Licensing Agreement ("Agreement") is entered into this 1st day of September 2016 ('Effective Date') by and between Emerald Health Sciences Inc. ('EHS'), Emerald Health Nutraceuticals Inc. ('EHN'), and Michael T. Murray, N.D. ('Dr. Murray'). This Agreement sets forth a description of those responsibilities of EHS, EHN, and Dr. Murray, of certain rights granted to EHS and EHN, and of certain other terms.

---

#### Segment 2: ARTICLE 2 -- RESPONSIBILITIES

| Property | Value |
|---|---|
| **Predicted Class** | `PAYMENT` |
| **Confidence** | HIGH **77.0%** |
| Tokens | 284 |
| Source | direct |

**Body Text:**

> 2.1 EHS and EHN shall bear all costs associated with the development, inventory, sales, and marketing of any product ('Products') which EHS or EHN sells. 2.2 EHS: During any Services Term (defined below), Dr. Murray shall provide the following ongoing services to EHS for the compensation set forth in Article 5: (a) Provide guidance and/or lead initiatives related to the development of pharmaceutical forms of the EHS cannabinoid portfolio including methods to enhance bioavailability or delivery o... [truncated]

---

#### Segment 3: ARTICLE 3 -- DEFINITION OF SCOPE

| Property | Value |
|---|---|
| **Predicted Class** | `RENEWAL` |
| **Confidence** | LOW **40.7%** |
| **Alternatives** | RENEWAL (40.7%), ENTIRE_AGREEMENT (33.8%), ASSIGNMENT (8.8%) |
| Tokens | 61 |
| Source | direct |

**Body Text:**

> 3.1 Licensing rights. EHS and EHN agree that they shall not use Dr. Murray's name or likeness on its products or product marketing materials unless specifically approved by Dr. Murray by written acknowledgement including emails and facsimile transmissions of his approval.

---

#### Segment 4: 3.2 Exclusivity.

| Property | Value |
|---|---|
| **Predicted Class** | `DELIVERY_OBLIGATIONS` |
| **Confidence** | HIGH **96.0%** |
| Tokens | 28 |
| Source | direct |

**Body Text:**

> Dr. Murray shall not directly assist in the development of any product competitive to products developed by EHS or EHN.

---

#### Segment 5: 3.3 Additional Services.

| Property | Value |
|---|---|
| **Predicted Class** | `PAYMENT` |
| **Confidence** | HIGH **99.4%** |
| Tokens | 37 |
| Source | direct |

**Body Text:**

> Compensation for any other mutually agreed upon project that is outside the scope of this Agreement will be negotiated and mutually agreed upon by the parties.

---

#### Segment 6: ARTICLE 4 -- PROPERTY RIGHTS

| Property | Value |
|---|---|
| **Predicted Class** | `IP_OWNERSHIP` |
| **Confidence** | HIGH **99.8%** |
| Tokens | 122 |
| Source | direct |

**Body Text:**

> 4.1 EHS and EHN shall have the exclusive rights in and to all  ingredients, product specifications, goodwill, and all other intellectual property rights associated with any Product(s); provided, however, that EHS and EHN shall not have any rights in or to Dr. Murray's name or likeness except as expressly granted in writing herein or via electronic transmission by Dr. Murray. Neither shall Dr. Murray have any rights or interest whatsoever in any intellectual property, trademarks, trade names, ser... [truncated]

---

#### Segment 7: 5.1 Payment for Services.

| Property | Value |
|---|---|
| **Predicted Class** | `PAYMENT` |
| **Confidence** | HIGH **99.0%** |
| Tokens | 37 |
| Source | direct |

**Body Text:**

> EHN will pay Dr. Murray $8,333 per month at the end of each month during the first twelve months that this agreement is in effect.

---

#### Segment 8: 5.2 Options.

| Property | Value |
|---|---|
| **Predicted Class** | `RENEWAL` |
| **Confidence** | HIGH **82.7%** |
| Tokens | 71 |
| Source | direct |

**Body Text:**

> Upon execution of this Agreement and on each anniversary date of this Agreement for as long as this Agreement is active, EHS will grant Dr. Murray options to purchase 25,000 shares of EHS common stock at their then fair market value (the 'Options'). The Options will vest immediately on the date of grant.

---

#### Segment 9: 5.3 Royalty/Commission Payments.

| Property | Value |
|---|---|
| **Predicted Class** | `PAYMENT` |
| **Confidence** | HIGH **99.1%** |
| Tokens | 136 |
| Source | direct |

**Body Text:**

> Dr. Murray will receive an annual royalty on net sales (defined as gross sales minus returns) for any products (the 'Dr. Murray Products') developed by Dr. Murray for EHN for as long as the Dr. Murray Products are being sold. The Dr. Murray Products will be listed on Schedule A attached hereto as they are developed and added to product portfolio. During each year of this agreement, Dr. Murray will be paid no later than the 30 st  day of January based on the cumulative Net Sales of the Dr. Murray... [truncated]

---

#### Segment 10: 5.4 Ownership in EHN.

| Property | Value |
|---|---|
| **Predicted Class** | `WARRANTIES` |
| **Confidence** | LOW **36.0%** |
| **Alternatives** | WARRANTIES (36.0%), ENTIRE_AGREEMENT (31.7%), DELIVERY_OBLIGATIONS (19.0%) |
| Tokens | 32 |
| Source | direct |

**Body Text:**

> Upon execution of this agreement, EHN will issue to Dr. Murray sufficient shares to represent a 5% equity ownership in EHN.

---

#### Segment 11: 5.5 Expenses and Travel.

| Property | Value |
|---|---|
| **Predicted Class** | `PAYMENT` |
| **Confidence** | HIGH **99.6%** |
| Tokens | 105 |
| Source | direct |

**Body Text:**

> Any pre-approved expenses incurred by Dr. Murray on behalf of EHS or EHN during any Services Term will be reimbursed, including but not limited to travel expenses incurred for air travel, car rental, hotels and meals, subject to prior approval in each case. EHS or EHN agrees to reimburse Dr. Murray for all reasonable expenses (air travel, hotel, car rental, meals, materials, etc.) relating to EHS or EHN directed activities, subject to prior approval in each case.

---

#### Segment 12: 5.6 Withholdings and Taxes.

| Property | Value |
|---|---|
| **Predicted Class** | `PAYMENT` |
| **Confidence** | HIGH **99.6%** |
| Tokens | 44 |
| Source | direct |

**Body Text:**

> Dr. Murray shall be responsible for all federal or state withholdings and taxes, and shall indemnify EHS or EHN for any actions brought against EHS or EHN with respect thereto.

---

#### Segment 13: 5.7 Instructions for Payment.

| Property | Value |
|---|---|
| **Predicted Class** | `PAYMENT` |
| **Confidence** | HIGH **99.6%** |
| Tokens | 41 |
| Source | direct |

**Body Text:**

> All payments due and payable to Dr. Murray hereunder shall be paid to: Michael T. Murray, N.D. [intentionally omitted] Or via electronic transfer as directed by Dr. Murray

---

#### Segment 14: 5.8. EHS or EHN Benefits.

| Property | Value |
|---|---|
| **Predicted Class** | `PAYMENT` |
| **Confidence** | HIGH **83.7%** |
| Tokens | 88 |
| Source | direct |

**Body Text:**

> Dr. Murray and Dr. Murray acknowledge and agree and it is the intent of the parties hereto that except as set forth in Section 5, neither Dr. Murray nor any employees or contractors of Dr. Murray receive any EHS-sponsored benefits, either as a consultant or employee. Such benefits include, but are not limited to, paid vacation, sick leave, medical insurance, and 401(k) participation.

---

#### Segment 15: 6.1 Term.

| Property | Value |
|---|---|
| **Predicted Class** | `RENEWAL` |
| **Confidence** | HIGH **99.7%** |
| Tokens | 226 |
| Source | direct |

**Body Text:**

> This Agreement shall become effective as of the Effective Date and shall remain in effect as follows. (a) Dr. Murray's obligations set out herein shall be performed from the Effective Date until December 31, 2018 (the initial 'Services Term'). The Services Term of this Agreement shall be automatically renewed for successive two-year terms thereafter unless written notice is given by either party to the other, indicating that party's intention not to renew the Services Term of this Agreement, at ... [truncated]

---

#### Segment 16: 6.2 Termination

| Property | Value |
|---|---|
| **Predicted Class** | `TERMINATION` |
| **Confidence** | HIGH **99.7%** |
| Tokens | 72 |
| Source | direct |

**Body Text:**

> EHS or EHN, on the one hand, and Dr. Murray, on the other, may terminate any Services Term of this Agreement by delivering 60 days written notice to the other party. Notwithstanding the foregoing, EHS or EHN may immediately terminate the Services Term without notice should Dr. Murray be in breach of this Agreement.

---

#### Segment 17: 6.3 Effect of Termination.

| Property | Value |
|---|---|
| **Predicted Class** | `ENTIRE_AGREEMENT` |
| **Confidence** | HIGH **92.2%** |
| Tokens | 201 |
| Source | direct |

**Body Text:**

> (a) If a Services Term is terminated or expires but this Agreement is not otherwise terminated in accordance with Section 6.2, all other rights and obligations shall remain in effect following the termination or expiration of the Services Term. These include without limitation Sections 2.1, 3.1, 3.2, 4.1, 5.2, 5.3, 7, 8 and 9. (b) If this Agreement is termination in accordance with Section 6.2 by Dr. Murray, all of the rights and obligations hereunder shall cease and be of no further force or ef... [truncated]

---

#### Segment 18: ARTICLE 7 -CONFIDENTIAL INFORMATION

| Property | Value |
|---|---|
| **Predicted Class** | `CONFIDENTIALITY` |
| **Confidence** | HIGH **99.1%** |
| Tokens | 344 |
| Source | direct |

**Body Text:**

> Neither EHS, EHN nor Dr. Murray shall disclose to any third parties, except as required by law, at any time during or subsequent to the term of this Agreement, any Confidential Information. 'Confidential Information: includes proprietary information, technical data, trade secrets or know-how, including, but not limited to, the terms and conditions of this Agreement, research, product  plans, products, services, suppliers, customer lists and customers, prices and costs, markets, inventions, techn... [truncated]

---

#### Segment 19: ARTICLE 8 -- NOTICES

| Property | Value |
|---|---|
| **Predicted Class** | `NOTICES` |
| **Confidence** | HIGH **99.8%** |
| Tokens | 172 |
| Source | direct |

**Body Text:**

> All notices, communications, payments or other correspondence required to be given or made under this Agreement shall be in writing and shall be deemed received (a) on the same day if delivered in person, courier service, confirmed e-mail delivery, or facsimile transmission, (b) on the next day if delivered by next day Federal Express, UPS, or other reputable overnight carrier, or (c) within three (3) days if delivered by mail. All notices shall be given to the parties at the following addresses... [truncated]

---

## Document: License Agreement
**Source:** `DataCallTechnologies_20060918_SB-2A_EX-10.9_944510_EX-10.9_Content License Agreement.pdf`

**Stats:** 21 segments | Seg: 4.2s | Class: 1.6s | Filtered: 4 | Classified: 17 (High: 13, Review: 4)

#### Segment 1: CONTENT LICENSING AGREEMENT

| Property | Value |
|---|---|
| **Predicted Class** | `ENTIRE_AGREEMENT` |
| **Confidence** | LOW **38.8%** |
| **Alternatives** | ENTIRE_AGREEMENT (38.8%), DEFINITIONS (13.3%), ASSIGNMENT (10.0%) |
| Tokens | 44 |
| Source | direct |

**Body Text:**

> between Data Call Technologies, Inc. 600 Kenrick, Suite B-12 Houston, Texas 77060 hereinafter referred to as "Licensor" and PLAN_B MEDIA AG Schaafenstr. 25 50676 Cologne Germany hereinafter referred to as "plan_b"

---

#### Segment 2: 1    PURPOSE  OF  THE  AGREEMENT

| Property | Value |
|---|---|
| **Predicted Class** | `RENEWAL` |
| **Confidence** | HIGH **85.6%** |
| Tokens | 122 |
| Source | direct |

**Body Text:**

> 1.1  The purpose  of  this  content  distribution  Agreement  (hereinafter "Agreement")  is  to  set  forth  the terms and conditions under which plan_b  may  use  the  Content  ("Content" as set forth in APPENDIX 2) owned  or  licensed  by  LICENSOR  for  a  commercial  distribution to plan_b's  End  Users  in  the  territory  ("Territory" as set forth in APPENDIX  2). 1.2  End User  means  any  third  Party  receiving  Content  on  a  mobile device  for  a  payment in accordance with the terms... [truncated]

---

#### Segment 3: 2    OBLIGATIONS  OF  LICENSOR

| Property | Value |
|---|---|
| **Predicted Class** | `RENEWAL` |
| **Confidence** | MEDIUM **72.9%** |
| **Alternatives** | RENEWAL (72.9%), ENTIRE_AGREEMENT (19.2%), PAYMENT (3.4%) |
| Tokens | 118 |
| Source | direct |

**Body Text:**

> 2.1  LICENSOR  shall  make  a  first  delivery  of Content to plan_b within 14  days  after the signing of this Agreement unless separately agreed between  the  Parties. 2.2  LICENSOR  shall  deliver  Content  according  to  the  specifications (for  example  formats,  file  sizes)  set  by  plan_b or to be agreed between  the  Parties  in  writing. 2.3  LICENSOR  grants  plan_b  for  the  term  of  this Agreement the right to  produce,  market  and  distribute  Content  to  End  Users (in the t... [truncated]

---

#### Segment 4: 3    OBLIGATIONS  OF  PLAN_B

| Property | Value |
|---|---|
| **Predicted Class** | `REPORTING_AUDIT` |
| **Confidence** | HIGH **99.5%** |
| Tokens | 241 |
| Source | direct |

**Body Text:**

> 3.1  plan_b  will  distribute  Content  to  End  Users  in  the  Territory through  its  distribution  channels. 3.2  plan_b  shall  use  reasonable  commercial  efforts  to  market  and stimulate  interest  in  the  Content  with  its  customers. 3.3  plan_b  shall  provide  LICENSOR  with  a  detailed  written  record, which  includes the number of End User downloads and each distribution channel.  Such  report  shall  be  provided  to LICENSOR in electronic format  within  6  weeks  of  the  ... [truncated]

---

#### Segment 5: 4    REVENUES

| Property | Value |
|---|---|
| **Predicted Class** | `PAYMENT` |
| **Confidence** | HIGH **99.2%** |
| Tokens | 20 |
| Source | direct |

**Body Text:**

> 4.1  plan_b  shall  pay  LICENSOR  a  share of its revenues as set forth in

---

#### Segment 6: APPENDIX  2  ("REVENUES").

| Property | Value |
|---|---|
| **Predicted Class** | `PAYMENT` |
| **Confidence** | HIGH **84.8%** |
| Tokens | 22 |
| Source | direct |

**Body Text:**

> 4.2  All shares  are  net,  plus  the  respective  applying value added tax (if  applicable).

---

#### Segment 7: 5    INTELLECTUAL  PROPERTY  RIGHTS

| Property | Value |
|---|---|
| **Predicted Class** | `IP_OWNERSHIP` |
| **Confidence** | HIGH **99.8%** |
| Tokens | 278 |
| Source | direct |

**Body Text:**

> 5.1  LICENSOR  is  the  owner  of  all  intellectual  property  rights, including  without  limitation,  any  and all patents, utility models, trade  marks,  rights  in  designs,  trade,  business or domain names, know-how,  rights  in  databases and copyrights, rights in inventions, ideas, concepts, trade secrets and confidential information which have to  be  given  to  fulfill  this  contract. 5.2  In the  alternative,  if  LICENSOR  is  not  the  sole  and  exclusive owner  of  all  of  the ... [truncated]

---

#### Segment 8: 6    CONFIDENTIALITY

| Property | Value |
|---|---|
| **Predicted Class** | `CONFIDENTIALITY` |
| **Confidence** | HIGH **99.3%** |
| Tokens | 287 |
| Source | direct |

**Body Text:**

> 6.1  Each Party  shall  keep  in  confidence  all material and information, including  without  limitation  Content, received from the other Party and  marked  as  confidential  or  which  should  be  understood to be confidential,  and  may  not  use such material or information for any other  purposes  than  those  set  forth  in  this  Agreement.  The confidentiality  obligation shall, however, not be applied to material and  information,  which  as  shown  by  the  receiving  Party, 6.1.1 is... [truncated]

---

#### Segment 9: 7    TERM OF  THE  AGREEMENT  AND  TERMINATION

| Property | Value |
|---|---|
| **Predicted Class** | `RENEWAL` |
| **Confidence** | HIGH **99.5%** |
| Tokens | 300 |
| Source | direct |

**Body Text:**

> 7.1  Unless  otherwise  stated  in  the  Appendix  the  term of this letter Agreement  shall  continue  for  twenty-four  (24)  months  with  the effective  date  unless  terminated sooner or extended pursuant to the terms hereof ("Initial Term"). The Initial Term shall automatically be extended  for  an additional period of half a year unless either party provides  the  other party with written notification of termination of the  letter  Agreement  at  least 60 days prior to end of such period.... [truncated]

---

#### Segment 10: 8    MISCELLANEOUS

| Property | Value |
|---|---|
| **Predicted Class** | `ENTIRE_AGREEMENT` |
| **Confidence** | HIGH **88.5%** |
| Tokens | 126 |
| Source | direct |

**Body Text:**

> 8.1  The Parties  acknowledge  that  they  act  as  independent contractors and this Agreement does not constitute any partnership, joint venture, agency  relationship  or  other independent legal entity separate from the  Parties. 8.2  Neither  Party  shall  assign  or  transfer  to  any  third  party, without  the  prior written consent of the other Party, this Agreement or  any  rights  granted  herein. 8.3  Any amendments  to  this  Agreement  shall  be  in  writing  and shall have no effect... [truncated]

---

#### Segment 11: 9    SEVERABILITY

| Property | Value |
|---|---|
| **Predicted Class** | `ENTIRE_AGREEMENT` |
| **Confidence** | HIGH **94.1%** |
| Tokens | 122 |
| Source | direct |

**Body Text:**

> 9.1  In the  event  that  any  provision  in this Agreement will be subject to  an  interpretation  under which it would be void or unenforceable, such  provisions  will be construed so as to constitute it a valid and enforceable provision to the fullest extent possible, and in the event that  it  cannot  be  so construed, it will, to that extent, be deemed deleted  and  separable  from  the other provisions of this Agreement, which  will  remain  in full force and effect and will be construed t... [truncated]

---

#### Segment 12: 10   GOVERNING  LAW  AND  VENUE

| Property | Value |
|---|---|
| **Predicted Class** | `GOVERNING_LAW` |
| **Confidence** | HIGH **98.3%** |
| Tokens | 87 |
| Source | direct |

**Body Text:**

> 10.1 This Agreement  shall  be  governed  and  construed in accordance with the  laws  of  the  United  States of America. The courts of competent jurisdiction  at  New  York  City,  New York, shall have the exclusive jurisdiction  over  any  dispute  arising out of or in connection with this  Agreement. 10.2 This Agreement  has  been  prepared  in  two (2) identical copies, one for  each  Party.

---

#### Segment 13: APPENDIX  I — 1.   CONTACT  PLAN_B  MEDIA  AG

| Property | Value |
|---|---|
| **Predicted Class** | `NOTICES` |
| **Confidence** | HIGH **96.1%** |
| Tokens | 20 |
| Source | direct |

**Body Text:**

> Name:         Matthias  Hellmann Position:     Head  of  Content Phone:        XXXXXXXXXXXXX Email:        XXXXXXXXXXXXXXX

---

#### Segment 14: 2    CONTACT  LICENSOR

| Property | Value |
|---|---|
| **Predicted Class** | `SIGNATURE_BLOCK` |
| **Confidence** | FILTERED **100.0%** |
| Tokens | 18 |
| Source | filtered |

**Body Text:**

> Name:         Jim  Ammons Position:     CEO  /  President Phone:        866-219-2025 Email:        ammons@datacalltech.com

---

#### Segment 15: 3    CONTACT       LICENSOR  AGENT  (IF  APPLICABLE)

| Property | Value |
|---|---|
| **Predicted Class** | `SIGNATURE_BLOCK` |
| **Confidence** | FILTERED **100.0%** |
| Tokens | 13 |
| Source | filtered |

**Body Text:**

> Name: Position: Phone: Email:

---

#### Segment 16: 4   BANK  ACCOUNT  LICENSOR

| Property | Value |
|---|---|
| **Predicted Class** | `SIGNATURE_BLOCK` |
| **Confidence** | FILTERED **100.0%** |
| Tokens | 19 |
| Source | filtered |

**Body Text:**

> Bank name:     Bank  Of  America Bank address:  Dallas,  Texas Country:       USA

---

#### Segment 17: APPENDIX  2 — 1    CONTENT,  SHARE  &  TERRITORY

| Property | Value |
|---|---|
| **Predicted Class** | `RENEWAL` |
| **Confidence** | MEDIUM **48.2%** |
| **Alternatives** | RENEWAL (48.2%), ENTIRE_AGREEMENT (14.8%), PAYMENT (10.2%) |
| Tokens | 20 |
| Source | direct |

**Body Text:**

> 1.1  Contract  name (for internal plan_b-ware use): Data Call Technologies, Inc.

---

#### Segment 18: CONTENT                          LICENSOR       TERRITORY            TERMINATION

| Property | Value |
|---|---|
| **Predicted Class** | `REPORTING_AUDIT` |
| **Confidence** | LOW **37.1%** |
| **Alternatives** | REPORTING_AUDIT (37.1%), CONFIDENTIALITY (11.7%), LIABILITY_LIMITATION (10.5%) |
| Tokens | 85 |
| Source | direct |

**Body Text:**

> SHARE Top  News  Headlines Top  Business  Headlines Science/Health  News Entertainment  Headlines National  Football  League National  Basketball  Association National  Hockey  League Major  League  Baseball NCAA  Football NCAA  Men's  Basketball Professional  Golf  Association NASCAR Latest  Sports  Lines               45%        Worldwide             24  months Latest  Sports  Headlines Thought  for  Today Market  Details World  Financial  Highlights Weather: Current  Conditions 48-Hour  Forec... [truncated]

---

#### Segment 19: 2    TERMS

| Property | Value |
|---|---|
| **Predicted Class** | `RENEWAL` |
| **Confidence** | HIGH **99.7%** |
| Tokens | 28 |
| Source | direct |

**Body Text:**

> 2.1  Contract  start:  04-01-06 2.2  Contract  end:  04-01-08 2.3  Commercial  distribution  possible  from: 2.4  Sell-off  period:  3  months  after  termination

---

#### Segment 20: 3    PAYMENTS  AND  REPORTS  TO  LICENSOR

| Property | Value |
|---|---|
| **Predicted Class** | `PAYMENT` |
| **Confidence** | HIGH **99.7%** |
| Tokens | 31 |
| Source | direct |

**Body Text:**

> 3.1  Reporting:  Quarterly;  30  days  after  end  of  quarter 3.2  Payment  terms:  30  days  after  receipt  of  invoice

---

#### Segment 21: 4    EXCLUSIVITY  COPYRIGHT

| Property | Value |
|---|---|
| **Predicted Class** | `SIGNATURE_BLOCK` |
| **Confidence** | FILTERED **100.0%** |
| Tokens | 15 |
| Source | filtered |

**Body Text:**

> 4.1  Content  exclusive:  [ ]  Yes  [ ]  No

---

## Document: Service Agreement
**Source:** `IntegrityFunds_20200121_485BPOS_EX-99.E UNDR CONTR_11948727_EX-99.E UNDR CONTR_Service Agreement.pdf`

**Stats:** 8 segments | Seg: 7.1s | Class: 3.4s | Filtered: 0 | Classified: 8 (High: 3, Review: 5)

#### Segment 1: DISTRIBUTION AND SERVICES AGREEMENT January 18, 2020

| Property | Value |
|---|---|
| **Predicted Class** | `ENTIRE_AGREEMENT` |
| **Confidence** | MEDIUM **67.6%** |
| **Alternatives** | ENTIRE_AGREEMENT (67.6%), RENEWAL (17.7%), WARRANTIES (4.2%) |
| Tokens | 115 |
| Source | direct |

**Body Text:**

> This is to confirm that, in consideration of the agreements hereinafter contained, the undersigned, the Integrity Short Term Government Fund, (the 'Fund'), an open-end, diversified, management investment company organized as a series of The Integrity Funds, a Delaware statutory trust, has agreed that Integrity Funds Distributor, LLC, ('Integrity'), shall be, for the period of this distribution agreement (the 'Agreement'), the principal underwriter of shares issued by the Fund, including such cla... [truncated]

---

#### Segment 2: SECTION 1. SERVICES AS UNDERWRITER

| Property | Value |
|---|---|
| **Predicted Class** | `PAYMENT` |
| **Confidence** | MEDIUM **66.9%** |
| **Alternatives** | PAYMENT (66.9%), ENTIRE_AGREEMENT (15.2%), DELIVERY_OBLIGATIONS (13.0%) |
| Tokens | 1125 |
| Source | sub_chunk_average(3) |
| Oversized | Yes |

**Body Text:**

> Section 1.1 Integrity will act as principal underwriter for the distribution of the Shares covered by the registration statement, prospectus, and statement of additional information then in effect of the Fund (the 'Registration Statement') under the Securities Act of 1933, as amended (the '1933 Act'), and the Investment Company Act of 1940, as amended (the '1940 Act'). Section 1.2 Integrity agrees to use its best efforts to solicit orders for the sale of the Shares at the public offering price, ... [truncated]

---

#### Segment 3: SECTION 2. DUTIES OF THE FUND

| Property | Value |
|---|---|
| **Predicted Class** | `DELIVERY_OBLIGATIONS` |
| **Confidence** | HIGH **99.0%** |
| Tokens | 326 |
| Source | direct |

**Body Text:**

> Section 2.1 The Fund agrees at its own expense to execute any and all documents, to furnish any and all information, and to take any other actions that may be reasonably necessary in connection with the qualification of the Shares for sale in those states that Integrity may designate. Section 2.2 The Fund shall furnish from time to time, for use in connection with the sale of the Shares, such information reports with respect to the Fund and its Shares as Integrity may reasonably request, all of ... [truncated]

---

#### Segment 4: SECTION 3. REPRESENTATIONS AND WARRANTIES

| Property | Value |
|---|---|
| **Predicted Class** | `WARRANTIES` |
| **Confidence** | MEDIUM **46.5%** |
| **Alternatives** | WARRANTIES (46.5%), TERMINATION (44.2%), ENTIRE_AGREEMENT (7.5%) |
| Tokens | 552 |
| Source | sub_chunk_average(2) |
| Oversized | Yes |

**Body Text:**

> Section 3.1 The Fund represents to Integrity that all registration statements, prospectuses, and statements of additional information filed by the Fund with the SEC under the 1933 Act and the 1940 Act with respect to the Shares of the Fund have been carefully prepared in conformity with the requirements of the 1933 Act, the 1940 Act, and the rules and regulations of the SEC thereunder. As used in this Agreement, the terms 'registration statement,' 'prospectus,' and 'statement of additional infor... [truncated]

---

#### Segment 5: SECTION 4. INDEMNIFICATION

| Property | Value |
|---|---|
| **Predicted Class** | `INDEMNIFICATION` |
| **Confidence** | MEDIUM **67.0%** |
| **Alternatives** | INDEMNIFICATION (67.0%), NOTICES (16.0%), DISPUTE_RESOLUTION (10.9%) |
| Tokens | 1627 |
| Source | sub_chunk_average(4) |
| Oversized | Yes |

**Body Text:**

> Section 4.1 The Fund authorizes Integrity and any dealers with whom Integrity has entered into dealer agreements to use any prospectus or statement of additional information furnished by the Fund from time to time in connection with the sale of Shares. The Fund agrees to indemnify, defend, and hold Integrity, its several officers and governors, and any person who controls Integrity within the meaning of Section 15 of the 1933 Act, free and harmless from and against any and all claims, demands, l... [truncated]

---

#### Segment 6: SECTION 5. EFFECTIVENESS OF REGISTRATION

| Property | Value |
|---|---|
| **Predicted Class** | `ENTIRE_AGREEMENT` |
| **Confidence** | MEDIUM **63.6%** |
| **Alternatives** | ENTIRE_AGREEMENT (63.6%), WARRANTIES (28.0%), RENEWAL (3.6%) |
| Tokens | 195 |
| Source | direct |

**Body Text:**

> Section 5.1 None of the Shares shall be offered by either Integrity or the Fund under any of the provisions of this Agreement and no orders for the purchase or sale of the Shares hereunder shall be accepted by the Fund if and so long as the effectiveness of the registration statement then in effect or any necessary amendments thereto shall be suspended under any of the provisions of the 1933 Act or if and so long as a current prospectus as required by Section 5(b)(2) of the 1933 Act is not on fi... [truncated]

---

#### Segment 7: SECTION 6. NOTICE TO INTEGRITY

| Property | Value |
|---|---|
| **Predicted Class** | `NOTICES` |
| **Confidence** | HIGH **99.5%** |
| Tokens | 223 |
| Source | direct |

**Body Text:**

> Section 6.1 The Fund agrees to advise Integrity immediately in writing: (a) of any request by the SEC for amendments to the registration statement, prospectus, or statement of additional information then in effect or for additional information; (b) in the event of the issuance by the SEC of any stop order suspending the effectiveness of the registration statement, prospectus, or statement of additional information then in effect or the initiation of any proceeding for that purpose; (c) of the ha... [truncated]

---

#### Segment 8: SECTION 7. TERM OF AGREEMENT

| Property | Value |
|---|---|
| **Predicted Class** | `RENEWAL` |
| **Confidence** | HIGH **99.4%** |
| Tokens | 254 |
| Source | direct |

**Body Text:**

> Section 7.1 This Agreement shall continue until January 18, 2022, and thereafter shall continue automatically for successive annual periods ending on January 18th of each year, provided such continuance is specifically approved at least annually by (a) the Fund's Board of Trustees and (b) a vote of a majority (as defined in the 1940 Act) of the Fund's Trustees who are not interested persons (as defined in the 1940 Act) of the Fund and who have no direct or indirect financial interest in the oper... [truncated]

---

## Document: Supply Agreement
**Source:** `LohaCompanyltd_20191209_F-1_EX-10.16_11917878_EX-10.16_Supply Agreement.pdf`

**Stats:** 12 segments | Seg: 4.9s | Class: 1.9s | Filtered: 2 | Classified: 10 (High: 7, Review: 3)

#### Segment 1: PREAMBLE

| Property | Value |
|---|---|
| **Predicted Class** | `PREAMBLE` |
| **Confidence** | FILTERED **100.0%** |
| Tokens | 5 |
| Source | filtered |

**Body Text:**

> Fax No. :

---

#### Segment 2: SUPPLY CONTRACT

| Property | Value |
|---|---|
| **Predicted Class** | `NOTICES` |
| **Confidence** | HIGH **90.1%** |
| Tokens | 22 |
| Source | direct |

**Body Text:**

> The buyer/End-User: Shenzhen LOHAS Supply Chain Management Co., Ltd. ADD: Tel No. : The seller:

---

#### Segment 3: ADD:

| Property | Value |
|---|---|
| **Predicted Class** | `RENEWAL` |
| **Confidence** | LOW **40.1%** |
| **Alternatives** | RENEWAL (40.1%), DEFINITIONS (30.8%), ENTIRE_AGREEMENT (23.4%) |
| Tokens | 403 |
| Source | direct |

**Body Text:**

> The Contract is concluded and signed by the Buyer and Seller on , in Hong Kong. General provisions 1.1 This is a framework agreement, the terms and conditions are applied to all purchase orders which signed by this agreement (hereinafter referred to as the 'order'). 1.2 If the provisions of the agreement are inconsistent with the order, the order shall prevail. Not stated in order content will be subject to the provisions of agreement. Any modification, supplementary, give up should been written... [truncated]

---

#### Segment 4: 3. GOODS AND COUNTRY OF ORIGIN:

| Property | Value |
|---|---|
| **Predicted Class** | `LIABILITY_LIMITATION` |
| **Confidence** | LOW **40.1%** |
| **Alternatives** | LIABILITY_LIMITATION (40.1%), DEFINITIONS (16.8%), PAYMENT (11.2%) |
| Tokens | 293 |
| Source | direct |

**Body Text:**

> Specific order: The products quantity, unit price, specifications, delivery time and transportation, specific content shall be subject to the purchase order issued by entrusted party which is commissioned the buyer. 5. PACKING: To be packed in new strong wooden case(s) /carton(s), suitable for long distance transportation and for the change of climate, well protected against rough handling, moisture, rain, corrosion, shocks, rust, and freezing. The seller shall be liable for any damage and loss ... [truncated]

---

#### Segment 5: 9. PORT OF DESTINATION: SHENZHEN, GUANGDONG, CHINA

| Property | Value |
|---|---|
| **Predicted Class** | `LIABILITY_LIMITATION` |
| **Confidence** | HIGH **98.5%** |
| Tokens | 195 |
| Source | direct |

**Body Text:**

> INSURANCE: To be covered by the Seller for 110% invoice value against All Risks and War Risk. PAYMENT: Under Letter of Credit or T/T: Under the Letter of Credit: The Buyer shall open an irrevocable letter of credit with the bank within 30 days after signing the contract, in favor of the Seller, for 100% value of the total contract value. The letter of credit should state that partial shipments are allowed. The Buyer's agent agrees to pay for the goods in accordance with the actual amount of the ... [truncated]

---

#### Segment 6: Under the T/T

| Property | Value |
|---|---|
| **Predicted Class** | `PAYMENT` |
| **Confidence** | HIGH **76.9%** |
| Tokens | 33 |
| Source | direct |

**Body Text:**

> The trustee of the buyer remitted the goods to the seller by telegraphic transfer in batches as agreed upon after signing each order.

---

#### Segment 7: 12. DOCUMENTS:

| Property | Value |
|---|---|
| **Predicted Class** | `PAYMENT` |
| **Confidence** | HIGH **97.7%** |
| Tokens | 106 |
| Source | direct |

**Body Text:**

> 12.1 (1) Invoice in 5 originals indicating contract number and Shipping Mark (in case of more than one shipping mark, the invoice shall be issued separately). One certificate of origin of the goods. Four original copies of the packing list. Certificate of Quality and Quantity in 1 original issued by the agriculture products base. One copy of insurance coverage (6) Copy of cable/letter to the transportation department of Buyer advising of particulars as to shipment immediately after shipment is m... [truncated]

---

#### Segment 8: 12.2

| Property | Value |
|---|---|
| **Predicted Class** | `PAYMENT` |
| **Confidence** | HIGH **92.0%** |
| Tokens | 29 |
| Source | direct |

**Body Text:**

> (1) Invoice in 3 originals indicating contract number and L/C number. (2) Final acceptance certificate signed by the Buyer and the Seller.

---

#### Segment 9: CIP

| Property | Value |
|---|---|
| **Predicted Class** | `PAYMENT` |
| **Confidence** | LOW **42.2%** |
| **Alternatives** | PAYMENT (42.2%), DISPUTE_RESOLUTION (37.1%), ENTIRE_AGREEMENT (13.0%) |
| Tokens | 1114 |
| Source | sub_chunk_average(3) |
| Oversized | Yes |

**Body Text:**

> The seller shall contract on usual terms at his own expenses for the carriage of the goods to the agreed point at the named place of destination and bear all risks and expenses until the goods have been delivered to the port of destination. The Sellers shall ship the goods within the shipment time from the port of shipment to the port of destination. Transshipment is allowed. Partial Shipment is allowed. In case the goods are to be dispatched by parcel post/sea-freight, the Sellers shall, 3 days... [truncated]

---

#### Segment 10: 21. Law application

| Property | Value |
|---|---|
| **Predicted Class** | `GOVERNING_LAW` |
| **Confidence** | HIGH **99.7%** |
| Tokens | 41 |
| Source | direct |

**Body Text:**

> It will be governed by the law of the People's Republic of China ,otherwise it is governed by United Nations Convention on Contract for the International Sale of Goods.

---

#### Segment 11: 22. <<Incoterms 2000>>

| Property | Value |
|---|---|
| **Predicted Class** | `RENEWAL` |
| **Confidence** | HIGH **75.8%** |
| Tokens | 115 |
| Source | direct |

**Body Text:**

> The terms in the contract are based on (INCOTERMS 2000) of the International Chamber of Commerce. 23. The Contract is valid for 5 years, beginning from and ended on . This Contract is made out in three originals in both Chinese and English, each language being legally of the equal effect. Conflicts between these two languages arising there from, if any, shall be subject to Chinese version. One copy for the Sellers, two copies for the Buyers. The Contract becomes effective after signed by both pa... [truncated]

---

#### Segment 12: THE SELLER: SIGNATURE:

| Property | Value |
|---|---|
| **Predicted Class** | `SIGNATURE_BLOCK` |
| **Confidence** | FILTERED **100.0%** |
| Tokens | 6 |
| Source | filtered |

**Body Text:**

> SIGNATURE: 6

---

## SUMMARY

| Metric | Count |
|---|---|
| Total Classified | 80 |
| Total Filtered (Preamble/Signature) | 11 |
| High Confidence (>=75%) | 53 |
| Needs Review (<75%) | 27 |
| High Confidence Rate | 66.2% |
