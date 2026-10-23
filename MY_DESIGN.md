# MY_DESIGN.md: Revenue Leakage Detection

## 1. Problem
Ek mid-size company har saal hazaron vendor invoices pay karti hai. Un me se kuch invoices galti se ya jaanbujhkar galat hote hain (double payment, limit se bachne ke liye tode gaye bills, bade ya ajeeb amounts), aur company ka paisa chupchap leak ho jata hai. Manual checking me ye sab pakadna mushkil hai. Mera project in invoices ko automatically dhundta hai aur rupees me batata hai ki kitna paisa risk me hai.

## 2. Meri 5 rules aur kyun

| # | Rule | Kya pakadta hai | Kyun zaroori hai |
|---|------|-----------------|------------------|
| 1 | Duplicate payment | Same vendor, same amount, 7 din ke andar dobara invoice | Sabse seedha paisa leak: ek hi kaam ka do baar payment |
| 2 | Split invoices | Ek bada bill 2-4 tukdo me, har tukda approval limit (Rs 50,000) se thoda neeche | Log badi approval se bachne ke liye bill todte hain, ye fraud ka classic sign hai |
| 3 | Weekend/holiday payment | Shanivar, Ravivar ya chhutti ke din hua payment | Office band hone par payment kam hi hota hai, to ye suspicious ya bina approval ka ho sakta hai |
| 4 | Round figure | Bilkul 10,000 ke multiple wale invoices (jaise 40,000, 70,000) | Asli bills me aksar paise ajeeb hote hain; bilkul round amount banawati ho sakta hai |
| 5 | Outlier | Vendor ke apne normal amount se bahut zyada (3 standard deviation se upar) | Achanak bahut bada bill overbilling ya galti ka sign hai |

## 3. Leakage aur Exposure ka fark
- **Confirmed leakage:** wo paisa jo lagbhag pakka ja chuka hai (duplicate payment ki poori amount + outlier me normal se extra amount).
- **Exposure at risk:** wo paisa jo suspicious hai par pakka loss nahi (split, round figure, weekend). Isme auditor ko jaanch karni padegi.

Dono ko alag rakhta hu, kyunki "suspicious" ko "pakka loss" bol dena galat hoga.

## 4. Dashboard me sabse upar
1. Total invoices aur total spend
2. **Confirmed leakage (Rs)**
3. **Exposure at risk (Rs)**
4. Kitne % invoices flag hue

Uske neeche: rule-wise breakdown, top 10 risky vendors, top 20 suspicious invoices, monthly trend.

## 5. Success ka matlab (kaise naapunga)
Data synthetic hai aur usme anomalies mene khud daali hain (ground truth file), isliye main check kar sakta hu ki rules ne kitni sahi pakdi.

- **Precision:** jitne invoices flag kiye, un me se kitne sach me anomaly the. Target: 80% se upar (kam false alarms).
- **Recall:** jitni anomalies thi, un me se kitni pakdi gayi. Target: 80% se upar.
- Har rule ka precision aur recall alag report karunga.
- Agar kisi rule ka score 100% aaye to maan lunga ki synthetic data zyada aasan hai, aur ye limitation README me likhunga.

## 6. Limitations (jo mujhe pata hai)
- Data synthetic hai, real company ka nahi.
- Rules simple hain, machine learning nahi.
- Holiday list adhuri hai.
- Real data me false alarms zyada honge.