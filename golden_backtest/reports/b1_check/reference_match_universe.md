# 참조 구현 대조 (P1 496종목, 2013-01-01 이후 데이터, 신호 2015-01-01 이후, 워밍업 게이트 252봉 적용)

- 비교한 거래: 18975건
- 참조 구현(터틀 원전 N 시드): 엄격 허용오차(상대 1e-8)에서 일치 18911건, **불일치 64건**(종목 41개)
- 같은 비교를 느슨한 허용오차(상대 1e-4)로: **불일치 0건**, 그중 날짜·청산 사유가 다른 구조적 불일치 0건
- 엄격 허용오차 불일치의 r 최대 절대 차이: 5.38e-05
- 참조 구현(진단용, 엔진과 같은 N 시드): 엄격 허용오차에서 불일치 **0건**

엄격 허용오차 불일치 종목(거래 수): ABNB 1, ALLE 2, ANET 1, BE 2, CARR 1, CEG 1, CFG 1, COIN 1, CRWD 2, CVNA 1, DDOG 1, DELL 2, DOW 1, EXE 1, FOXA 1, FTV 2, GDDY 2, GEHC 2, GEV 1, HLT 1, HOOD 3, HPE 3, HWM 1, INVH 1, IR 1, KHC 3, KVUE 1, LITE 1, MRNA 5, OTIS 1, P 1, PYPL 3, RDDT 1, SNDK 1, SOLV 1, UBER 2, VEEV 1, VLTO 1, VRT 3, VST 1, XYZ 2