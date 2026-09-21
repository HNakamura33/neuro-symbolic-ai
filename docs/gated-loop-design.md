# ゲート付きエージェントループの設計 — ReAct と文脈の形式化から導く

**設計案 v0.2** — 2026-09-22(v0.1: 2026-09-21)。本書は、ReAct[^react] 型エージェントループと文脈(context[^context])という基礎から出発し、「完了判定をモデルの自己申告から決定論的な関門[^gate]へ移す」ための最小の構造変更を算法として導出する。実装(SDK[^sdk] のフック[^hook])は最終節にのみ置き、本体は実装に依存しない。

位置付け: nsai の実験結果(記号層の寄与の符号は「有無」でなく「取り付け方」が決める; [paper-draft.md](paper-draft.md))を、プロンプト契約ではなくループの制御構造として実現し、ハーネス[^harness]の一形態として公開するための土台。ハーネス構造問題の枠組み(線形トランスクリプト・ボトルネック[^ltb]、六つの構造的問題)は別リポジトリ `harness-resarch` の調査レポート(2026-08-12)と研究レビュー(第 2 版 2026-09-21)に従う。

凡例は文末の脚注にまとめる。本文中の記号・略号・外国語は初出で脚注へリンクする。参考文献は §15 に番号付きで置き、本文からは [B1] のように参照する(全件、一次情報源で書誌検証済み: 2026-09-22)。

### v0.2 の変更点(基礎文献による再検討)

| 変更 | 根拠となる基礎 | 影響する節 |
|---|---|---|
| 関門に参照モニタ[^refmon]の三要件(常に呼ばれる・改竄不能・小さく検査可能)を課す | Anderson 1972 [C1]、Saltzer & Schroeder 1975 [C2] | 新 §2.5、§4、§8、§11 |
| 関門が強制できるのは安全性[^safety]だけであり、生存性[^liveness]は強制できないことを明記。拒否上限と fail-closed[^failclosed] は調整項ではなく構造的必然 | Schneider 2000 [C3]、Ligatti ら 2005 [C4] | §7 |
| 検証器を閉世界[^cwa]の完全性制約[^ic]として定義し、開世界[^owa]推論を検証に流用しない。宣言的な形状[^shacl]として記述する | Reiter 1988 [B6]、Tao ら 2010 [B8]、Patel-Schneider 2015 [B9]、SHACL [B7] | §8、§11 |
| 証明書を証明携行[^pcc]の枠組みで位置付け、検証器を信頼計算基盤[^tcb]、π を非信頼の生成者とする | Necula 1997 [C6]、Goldwasser ら 1989 [C9] | §5 |
| 施行変種を遮蔽[^shield]の配置(先制・事後)と Simplex[^simplex] の切替に対応付け、先制型 GC[^gabc] を追加(SDK の MCP サーバー切替で実現可能) | Alshiekh ら 2018 [C8]、Sha 2001 [C7] | §6、§13 |
| Forced-CHECK[^fc] 論文の「要確認」を解消: 同論文は検査の**起動**を強制するが、確認した範囲では受理を検査結果に**条件付けていない** | Yi & Song 2026 [A13](要旨と本文抜粋を確認) | §6、§12 |
| 状態分離の先例(作業記憶/長期記憶、黒板[^blackboard]、信念基盤)と台帳の先例(真理維持系[^tms])を明記 | Soar[^soar] [B1]、ACT-R[^actr] [B2]、Hearsay-II[^hearsay] [B3]、BDI[^bdi] [B4]、CoALA[^coala] [A6]、Doyle 1979 [B5] | §3、§5 |
| 既存ソフトウェアにおける関門の層(字句・型・流れ・意味)を整理し、本設計の位置を意味層に定める | Outlines[^outlines] [A10]、Instructor[^instructor] [D6]、TypeChat[^typechat] [D7]、NeMo Guardrails[^nemo] [A11]、DSPy Assertions[^dspy] [A9]、運用系の関門 [D1]〜[D3]、各社ハーネスのフック [D3b]〜[D9] | 新 §14 |
| Stop フックは 8 回連続の差し戻しで CLI[^cli] 側が強制終了する仕様を確認。R < 8 とし、採点は証明書のみから行う | Claude Code フック仕様 [D8] | §7、§12、§13 |

---

## 1. ReAct ループの形式化

エージェントの状態を、時刻 t までのトランスクリプト[^transcript] T_t とする。方策[^policy] π はモデル本体であり、T_t だけを見て次の行為 a_t を確率的に選ぶ。ReAct [A1] はこの「推論の痕跡と行為を交互に T へ追記する」形を定式化し、Toolformer [A2] は道具呼び出しの決定そのものを π に学習させた。

```
a_t  ~  π( · | T_t )                       行為の選択(確率的)
a_t  ∈  Tools ∪ { FINAL(c) }                 道具呼び出し、または候補 c での完了宣言
o_t  =  env(a_t)                             道具の実行結果(観測)
T_{t+1} = T_t ⊕ a_t ⊕ o_t                   トランスクリプトへの追記(⊕ は連結)
停止:  a_t = FINAL(c) のとき、c を答えとして返す
```

```mermaid
flowchart LR
    T["T_t(トランスクリプト)"] --> P["π: 行為を選ぶ"]
    P -->|"道具呼び出し a_t"| E["env: 実行"]
    E -->|"観測 o_t"| A["T_{t+1} = T_t ⊕ a_t ⊕ o_t"]
    A --> T
    P -->|"FINAL(c)"| S(["停止: c を返す"])
```

この定式化で重要なのは、停止条件が **π が FINAL を出すこと** であり、それ以外の何物でもない点である。停止は T_t の関数であり、しかもその関数を計算するのは、行為を選ぶのと同じ確率的方策 π である。

## 2. 文脈の三重役割と、問題③の形式的表現

T は同時に三つの役割を担っている。

1. **作業記憶** — 途中結果、仮説、計画はすべて T に書かれる。
2. **モデルと世界を結ぶ唯一の通路** — 道具の入出力はすべて T を経由する。
3. **「完了した」の唯一の根拠** — 完了宣言 FINAL(c) が正しいかどうかを判断する材料も T しかない。

三役の同居が、調査レポート §3 が「線形トランスクリプト・ボトルネック」と呼ぶ共通根である(Backus [B10] のフォン・ノイマン・ボトルネックの類推)。六つの構造的問題のうち問題③(検証が構造化されていない)は、この定式化では次の一文になる。

> 停止述語 `stop(T_t) := [a_t = FINAL(c)]` は T_t のみの関数であり、世界の状態 W にも記号状態 S にも依存しない。しかも π 自身が計算する。

π が自分の出力を自分で確かめることに頼れないのは、経験的にも確定している。Huang ら [A12] は外部フィードバックのない内在的自己修正が性能をむしろ下げうることを示し、Reflexion [A5] の改善も環境からの外部フィードバックを前提とする(両論文の解説は `harness-resarch/articles/03-verification/` にある)。Kambhampati ら [A7] は計画の文脈で「LLM は自力では計画も自己検証もできない」と位置付け、外部のモデルに基づく検証器との双方向ループ(LLM-Modulo[^modulo])を提案した。nsai の測定はこれを問答で再現している。記号道具を任意の道具として与えた条件(C1[^c1])では、検証道具の使用率が負荷とともに落ち(1-hop 49% → 3-hop 12%)、純ニューラルな探索基線に負ける(EM[^em] 0.790 対 0.860)。プロンプト契約で検証を義務化した条件(rev5[^rev5])は 0.970 に達したが、義務化の主体は依然として π であり、契約を守るかどうかは π の確率的な指示遵守に委ねられている。

### 2.5 基礎からの要請

上の診断は新しくない。計算機セキュリティ、実行時検証、制御工学、データベース理論は、それぞれ「信頼できない主体の出力を、信頼できる小さな機構で仲介する」問題を半世紀前から扱ってきた。そこから本設計が受け取る要件を先に列挙する。

| 要件 | 内容 | 出典 | 本書での実現 |
|---|---|---|---|
| **R1 完全仲介** | 関門は常に呼ばれ、迂回経路がない | Anderson [C1] 「常に呼ばれなければならない」、Saltzer & Schroeder [C2] 完全仲介(complete mediation) | §4: submit が唯一の出口、Stop は後詰め |
| **R2 改竄不能** | π は関門の入力も判定も書き換えられない | Anderson [C1] 「改竄不能でなければならない」 | §8: S_0 の固定と S_agent の隔離 |
| **R3 小さく検査可能** | 関門は「分析と試験にかけられるほど小さい」 | Anderson [C1] | §11: 検証器を宣言的な形状として書く |
| **R4 安全性のみ** | 実行監視で強制できるのは安全性(悪いことが起きない)であり、生存性(良いことがいつか起きる)は強制できない | Schneider [C3] | §7: 「未検証の FINAL を出さない」は強制できる。「いつか正答する」は強制できないので、上限と unknown が要る |
| **R5 閉世界検証** | 完全性制約は世界についての言明ではなく「知識基盤が何を知っているか」への問い合わせであり、推論規則ではない | Reiter [B6]、Tao ら [B8]、Patel-Schneider [B9] | §8、§11: 検証器は主張済み三つ組への閉世界の問い合わせ。開世界推論を検証に流用しない |
| **R6 証明携行** | 生成者は信頼せず、生成物に添えた証明書を小さな検証器が安価に検査する | Necula [C6]、Goldwasser ら [C9] の証明者/検証者の非対称性 | §5: 証明書と台帳 |
| **R7 fail-safe 既定** | 判定できないときは拒否側に倒す | Saltzer & Schroeder [C2] 安全側既定(fail-safe defaults) | §7: unknown への降着 |

以下の各節は、この表の「本書での実現」を順に埋める。

## 3. 最小の構造変更: 状態の分離と停止述語の移管

状態を分ける。

| 記号 | 名前 | 中身 | 誰が書くか |
|---|---|---|---|
| T | ニューラル状態 | トランスクリプト | π と道具の出力 |
| S_0 | 記号状態(固定断面) | 課題開始時の知識グラフ[^kg]の断面と出典記録[^prov] | 誰も書かない(π は読むだけ) |
| L | 関門台帳[^ledger] | 検証器の実行記録と受理フラグ | 検証器と関門のみ(π は書けない) |
| S_agent | 記号状態(作業層) | π が課題中に追加した三つ組と、π が起動した閉包の結果 | π(道具経由) |
| W | 世界 | ファイル、環境、外部システム | 道具 |

以下、S = S_0 ∪ L と書く。S_agent は検証器の視界に入らない(§8)。

この分離には先例がある。Soar[^soar] [B1] は作業記憶と生成規則記憶を分け、行き詰まり(impasse)から下位目標を自動生成する。ACT-R[^actr] [B2] は宣言的記憶モジュールと生成システムを分ける。Hearsay-II [B3] の黒板[^blackboard]は、独立した知識源が共有の構造化状態に書き込み、制御部が次に動く知識源を選ぶ。BDI[^bdi] 型エージェント [B4] は信念基盤を熟慮から分離し、信念は知覚を通してのみ更新される。LLM エージェントの文脈では CoALA[^coala] [A6] が「作業記憶と長期記憶」「内的行為と外的行為」「意思決定手続き」の三軸でこれを整理した。本設計の T は作業記憶、S_0 は信念基盤、L は次節で述べる正当化記録に対応する。既存の枠組みと違うのは、**停止述語を作業記憶の外に置く** 点だけである。

```
受理:  FINAL(c) が受理される  ⇔  G(c, S) = pass
       G は決定論的関門。π は候補を提案するだけで、受理はループが決める。
```

これが本設計の全文である。π は「提案者」に、ループは「決定者」になる。調査レポート §4(c)「決定論的ゲートをプロンプトではなくループの制御構造に埋め込む」の形式化にあたる。

```mermaid
flowchart LR
    T["T(ニューラル状態)"] --> P["π: 提案"]
    P -->|"道具呼び出し"| E["env / 検証器"]
    E -->|"観測"| T
    E -->|"判定を記帳"| S["S(記号状態: 知識グラフ断面・出典・台帳)"]
    P -->|"submit(c)"| G{"G(c, S)"}
    S --> G
    G -->|"pass"| OK(["受理: c + 証明書"])
    G -->|"reject / 理由 r"| T
```

## 4. FINAL を型付き行為にする(R1 完全仲介)

現行の実験ハーネスは、自由文の末尾から正規表現[^regex]で `FINAL:` 行を抜き出している。これはボトルネックの縮図である。完了宣言が T の中の文字列にすぎないため、ループは「宣言があった」ことしか知らず、「何を根拠に」を知らない。

完了宣言を道具呼び出しにする。

```
submit(candidate: str, evidence: list[EvidenceRef])
```

- `candidate` — 答えそのもの(知識グラフ問答なら CURIE[^curie]、主張判定なら三値、監査なら違反対の集合)。
- `evidence` — 台帳の項目を指す参照。π が「この検査に通した」と主張する根拠。

道具呼び出しにする理由は三つある。第一に、関門が候補文字列を **構造化された入力として** 受け取れる(T を再解析しない)。第二に、受理・拒否の判定と理由を **道具の実行結果として** T に返せるので、π は拒否理由を次の行為に使える。第三に、Stop[^stop] 時点の情報は「トランスクリプトのパス」だけであり(§13)、そこで関門を組むと T の再解析に逆戻りする。

R1 の要点は「迂回経路がない」ことである。git の pre-commit フック [D3] は `--no-verify` で迂回でき、Claude Code の Stop フックは 8 回連続の差し戻しで CLI 側が強制終了する [D8]。前者は設計上の迂回、後者は運用上の安全弁だが、どちらも「関門が常に呼ばれる」を破る。本設計では、**採点と下流の消費は証明書のみから行う**ことで迂回を無害化する。submit を経ずにループが終わった場合、証明書は存在せず、結果は unknown として扱われる。迂回しても「未検証の答えが受理される」経路は生じない。

Stop は「submit せずに止まった」場合の fail-closed な後詰めとしてのみ使う。

## 5. 台帳と証明書(R6 証明携行)

関門台帳 L ⊂ S は、検証器の実行を一件ずつ記録する。

```
L の項目 = ( validator_id, candidate_exact, inputs, verdict ∈ {pass, warn, reject}, evidence_ptr, t )
```

受理条件は次の通り。

> 候補 c について、必須検証器集合 V_req のすべてに対し、`candidate_exact = c` の項目が存在し、その verdict が pass であること。

「どこかで検証器が呼ばれた」では足りない。**この候補文字列そのもの** に対する合格記録が要る。候補を変えれば台帳は無効になり、再検証が要る。これは rev5 で観測した「検証は通したが別の候補を答える」逸脱を構造的に閉じる。

受理された FINAL は証明書[^cert]を伴う。

```
証明書 = ( c, [L の該当項目], S_0 の断面識別子, 関門版 )
```

この構図は証明携行コード [C6] と同じである。信頼できない生成者(π)が生成物(c)に証明(台帳項目)を添え、信頼計算基盤に属する小さな検証器が、外部に問い合わせることなく安価に検査する。証明者は強力で検証者は効率的、という非対称性は対話型証明系 [C9] の前提でもある。本設計における検証は S_0 上の決定論的照合であり、生成(π の探索)より桁違いに安い。証明書は T の外(S)に残り、後から第三者が同じ S_0 の断面に対して再計算できる。

台帳の項目は、真理維持系[^tms] [B5] の正当化記録にあたる。Doyle の TMS は「信念の理由を記録し維持する」ことで依存関係に基づく撤回を可能にし、de Kleer の ATMS[^atms] [B5b] は仮定集合を明示することで矛盾した情報の下でも効率よく動く。本設計の台帳は S_0 の断面識別子を仮定集合として持つので、S_0 が変わったとき(知識基盤の更新)にどの証明書が失効するかを機械的に判定できる。この失効判定は論文 2(自己進化)の L1 検証済みレッスン保存庫が使う。

近接する提案との差を一文で述べる。ECT[^ect] [A14] は行為列の水準で「痕跡に束縛された型付き証明書 + 決定論的再生」を要求し、LLM-Modulo [A7] は計画の水準で「生成と外部検証器の双方向ループ」を置く。本設計は **答えの水準** で、OWL-RL[^owlrl] 推論器を備えた知識グラフ上の照合を、候補文字列に束縛された台帳として受理条件に組み込む。

## 6. 施行変種 — 遮蔽の配置として

ループ側の施行には、遮蔽 [C8] の配置に対応する三つの変種(GA・GB・GC[^gabc])がある。Alshiekh らの遮蔽は、学習方策の行為を監視し「仕様違反を起こす行為だけを訂正する」決定論的な層であり、方策が選ぶ前に選択肢を絞る **先制型(preemptive)** と、方策が選んだ後に訂正する **事後型(post-posed)** がある。

| 変種 | 遮蔽の配置 | 検証器を呼ぶ主体 | ループの役割 | 決定論的な部分 |
|---|---|---|---|---|
| **GA 義務型** | 事後 | π(道具として呼ぶ) | submit 時に台帳を照合し、欠落があれば構造化理由 r で拒否 | 台帳の照合、拒否、検証器の判定 |
| **GB 自動型** | 事後 | ループ自身 | submit 時に V_req を自ら実行して台帳に記帳し、結果で受理・拒否 | 検証器の起動も含めてすべて |
| **GC 先制型** | 先制 | π | 台帳に pass がない間は submit 道具を **提示しない**(道具集合を動的に絞る) | 選択肢の制限そのもの |
| (対照)rev5 | なし | π(プロンプトで義務化) | なし(正規表現で FINAL を抜くだけ) | 検証器の判定のみ。呼ぶかどうかは π |

GA・GB・GC はいずれもループ強制であり、rev5 はプロンプト強制である。GA と rev5 の差は「契約の施行主体」、GA と GB の差は「検証器の起動主体」、GA と GC の差は「拒否か選択肢の除去か」を分離する。GC は π に拒否理由を返さない代わりに、そもそも誤った行為を選べなくする。SDK は会話の途中で MCP サーバー単位の有効・無効を切り替えられる(`toggle_mcp_server`、§13)ので、submit を専用の MCP サーバーに置けば GC は実装できる。ただし実装は GA・GB を先行させる。

**Forced-CHECK[^fc] 論文との関係(v0.1 の「要確認」を解消)。** Yi & Song [A13] はハーネスを学習可能な制御層と見なし、制御器が observe / retrieve / call-tool / draft / check / revise / submit の七つの構造的行為を選ぶ(各行為の意味は領域アダプタが定める)。対照の Forced CHECK は check 行為を機械的に挿入する基線であり、コーディングでは利得 +0.0(学習制御器は +10.0)で、著者らは「機械的な検証では利得を説明できない」と結論する。確認した範囲(要旨と本文抜粋)では、同論文の submit を check の結果に条件付ける記述は見当たらない。「下書きの後に submit する、という規則は、下書きがもっともらしいが未検証のときに失敗する」という一文はあるが、それは学習制御器の動機付けであって受理条件ではない。つまり Forced CHECK が強制するのは検査の **起動** であり、本設計が強制するのは検査結果に基づく **受理** である。両者は別の介入であり、緊張関係にはない。むしろ Forced CHECK の null 結果は「検査を呼ばせるだけでは足りない」という本設計の前提(§12 の実測: rev5 は 600/600 で検査を呼んでいる)と整合する。

## 7. 有界性と fail-closed(R4 安全性のみ、R7 fail-safe 既定)

関門はなぜ上限と unknown を要するのか。Schneider [C3] は、実行を監視して介入する機構(実行監視器)が強制できる方針は安全性の性質に含まれることを示した。安全性とは「悪い有限接頭辞が現れない」こと、生存性とは「良いことがいつか起きる」ことである。「未検証の候補を受理しない」は安全性であり、関門はこれを強制できる。「いつか正しい答えを出す」は生存性であり、どんな監視器も強制できない。したがって、拒否を無限に続ければループは停止しないままになりうる。上限を置いて unknown に降着させるのは調整項ではなく、R4 からの構造的帰結である。

Ligatti ら [C4] の編集オートマトン[^editauto]は、監視器の介入を「打ち切り・抑制・挿入」に分類する。本設計の拒否は、submit 行為の抑制と、拒否理由 r の T への挿入との組み合わせであり、編集オートマトンの一例である。上限到達時の unknown は、Simplex アーキテクチャ [C7] における「高性能だが未検証の制御器から、単純で検証済みの基準制御器への切替」に相当する。基準制御器の出力が「わからない」であることが、fail-safe 既定 [C2] の具体化である。

```
拒否回数の上限 R(既定 3、上限 7)。submit の拒否と Stop の差し戻しを合算して数える
非 submit 行為の上限 N(手番上限)、または課題あたりの時間予算
各検証器の実行時間上限(既存: SPARQL[^sparql] 30 秒、閉包 120 秒、Z3[^z3] 30 秒、CSP[^csp] 探索空間 5M)
いずれかの上限到達時:  FINAL(unknown) を返す。未検証の候補は決して返さない。
```

R の上限 7 は Claude Code の仕様(Stop の差し戻し 8 回連続で CLI が強制終了 [D8])に由来する。関門が自分の意思で unknown に降着する回数を、CLI の安全弁より手前に置く。

停止性は次の性質として述べる。π の各ステップが有限時間で返り、検証器が全域[^total]で、拒否回数が R で、非 submit 行為が N で、それぞれ有界なら、ループは有限ステップで「証明書付きの c」か「unknown」のいずれかで停止する。「未検証の c」で停止する経路は存在しない。N の上限がなければ、submit も停止もせずに探索道具を呼び続ける経路が残るため、N は省けない。

## 8. 環境の硬化 — 関門を π の手の届かない場所へ(R2 改竄不能、R5 閉世界検証)

本設計には塞ぐべき穴がある。nsai の道具集合のうち、知識グラフを書き換えるものが二つある。

| 道具 | 書き換えの内容 | 危険 |
|---|---|---|
| `kb_add_triples` | 三つ組の追加 | π が「これから検査される事実」を自分で書き込んでから検査に通せる |
| `kb_infer` | OWL-RL 閉包の実体化 | 矛盾を含むグラフでは同一性述語(owl:sameAs)の連鎖が無関係な実体を併合し、誤答が「存在する」ようになる(監査で真の違反 5 件が 95 件に見えた原因) |

第一の穴は改竄(R2)である。調査レビュー §3.4 の三本柱(ii)「環境の硬化」、および RHB[^rhb] が示した「検証関数へのアクセス遮断だけでハック率を相対 88% 削減」は、この穴を放置してはならないことを示す。

第二の穴は、より根が深い。OWL[^owl] と RDF[^rdf] は開世界仮定の下にあり、「情報がないことは、ないことの情報ではない」[B9]。閉世界仮定の下では制約違反を引き起こす条件が、標準的な OWL 推論では **新しい推論を生む** [B8]。監査で 5 件が 95 件に膨らんだのは、まさにこの現象である。関数プロパティの衝突は、閉世界では「違反」だが、開世界推論では「二つの目的語は同一である」という含意を生み、同一性の連鎖が無関係な実体を併合していく。Reiter [B6] は 1988 年に、完全性制約は世界についての言明ではなく「知識基盤が何を知っていると言えるか」についての認識論的な問い合わせであり、形式的には「答えが yes でなければならない問い合わせ」と区別がつかないと論じた。検証器はこの意味の問い合わせであって、推論規則ではない。

規則を四つ置く。

1. **関門は課題開始時の断面 S_0 に対して評価する。** 検証器が読む知識グラフは課題開始時に固定した断面であり、`kb_add_triples` の書き込みは作業層 S_agent に積まれる。π の探索(`kb_find`、`kb_sparql`、`kb_path`)は S_0 ∪ S_agent を読んでよいが、検証器は S_0 だけを読む。
2. **閉包は検証器の内部で計算し、実体化しない。** `kb_verify` は必要な閉包を S_0 の複製上で計算して捨てる。π が起動した `kb_infer` の結果は S_agent に落ち、検証器の視界に入らない。
3. **否定的判定は閉世界で下す。** 「存在しない」「型が合わない」「違反である」の判定は、主張済みの S_0 に対する閉世界の問い合わせで行う。開世界推論(閉包)を使ってよいのは「含意される」という肯定的証拠を得るときだけであり、それも S_0 の複製上に限る。
4. **出典による除外。** 断面を固定できない対話モードでは、`source` が当該セッションのエージェント自身である三つ組を検証器の入力から除外する。

いずれも、§11 の「エージェント非書換」の性質を保証するための規則である。§3 の表で「π は S を書けない」としたのはこの意味であり、π の書き込みはすべて S_agent に隔離される。

## 9. 六つの構造的問題への写像

調査レポート §2 の六問題に対し、本設計は次の位置にある。

- **2.1 文脈を唯一の作業記憶とする設計** — 台帳と断面は S にあり T の長さに依存しない。関門は文脈が劣化しても同じ判定を返す。
- **2.2 サブエージェント分離の帯域ボトルネック** — 境界を越えるのは自由文ではなく証明書(§10)。
- **2.3 検証が構造化されていない** — 本設計の主題。停止述語を π から G へ移管する。
- **2.4 権限モデルの粒度不一致** — 関門は操作ごとの承認ではなく、受理条件という方針で書かれる。人間の判断が要るのは方針(V_req)の設計時であって実行時ではない(最小権限 [C2] の「必要な権限だけ」を、行為でなく受理条件の水準で与える)。
- **2.5 命令とデータの非分離** — 関門は S を読み、T を読まない。T に注入された「検証済み」という文字列は判定を偽造できない。判定は **計算されるもの** であって **引用されるもの** ではないからである。
- **2.6 ハーネスとモデルの共進化** — V_req は「現行モデルの限界についての仮定」の明示的な一覧である(調査レポート §4(d))。モデル更新で不要になった検証器は、台帳の pass 率が 1.0 に張り付くことで検出でき、取り外せる。

## 10. 委譲境界

サブエージェントへ委譲する場合、返ってくるのは自由文の報告ではなく証明書である。副エージェントの submit は親の台帳に記帳され、親は同じ受理条件で照合する。C2f[^c2f] で観測した「自由文の受け渡しで答えが失われる」(修正案 H1[^h1] で構造化受け渡しに修正)を形式化したもので、SubagentStop[^substop] を後詰めに使える。黒板 [B3] の語彙では、副エージェントは知識源であり、黒板(S)に書けるのは検証器を通った項目だけである。

## 11. 検証器のインターフェース(R3 小さく検査可能、R5 閉世界検証)

```
V : (candidate, task_context, S_0) → (verdict ∈ {pass, warn, reject}, evidence)
```

検証器に要求する性質:

| 性質 | 意味 | 出典 |
|---|---|---|
| 決定論的 | 同じ入力と同じ S_0 に対して常に同じ verdict | — |
| 全域 | 必ず有限時間で返る(時間上限で reject に落ちる) | R4 |
| 証拠付き | verdict の根拠(該当三つ組、反例、違反対)を S への参照として返す | R6 |
| モデル非依存 | π の種類・版に依存しない | 調査レポート §4(d) |
| エージェント非書換 | π が入力(S_0)を改変できない(§8) | R2 |
| 小さく検査可能 | 検証器の定義が人間に読め、単体で試験できる | R3 |

最後の性質は v0.2 で追加した。Anderson [C1] の第三要件「分析と試験にかけられるほど小さい」は、検証器を Python の手続きとして書く限り満たしにくい。知識グラフ上の検証器は、SHACL[^shacl] [B7] の形状として **宣言的に** 書くことを提案する。SHACL は RDF グラフの妥当性を形状(shape)の集合で検証する W3C 勧告であり、処理系に RDFS 推論を要求しない [B7b]。つまり閉世界の検証器を、推論と切り離して、データとして記述できる。

| 検証器 | 検査内容 | 課題 | v0.2 での記述形 |
|---|---|---|---|
| `kb_check_answer` | 存在・型・起点除外 | 知識グラフ問答 | SHACL 形状(存在: `sh:minCount 1`、型: `sh:path` + `sh:class`、起点除外: SPARQL 制約) |
| `kb_verify`(entailed 側のみ) | 最終ホップの三つ組の含意(S_0 の複製上の OWL-RL 閉包) | 知識グラフ問答、主張判定 | 手続き(推論を含むため)。**現行実装の `contradicted` は閉包上の関数プロパティ衝突から導かれる開世界経路であり、§8 規則 3 に反する。V_req に入れてよいのは entailed 側だけで、矛盾判定は `kb_violations` と同じく主張済み S_0 上の閉世界検査に置き換える** |
| `kb_violations` | 閉包前の関数プロパティ違反の列挙 | 矛盾監査 | SHACL 形状(`sh:maxCount 1`) |
| `smt_verify` | 数値・論理主張の証明または反例 | コーディング検証 | 手続き(Z3) |

宣言的に書くことで得られるもの: 検証器の定義そのものが S に置ける(出典付きで版管理できる)、π が読んでも書けない、モデルを変えても定義は変わらない、単体試験が形状とグラフの対で書ける。実装上の注記: SHACL 処理系 `pyshacl` は現行の依存に含まれておらず(rdflib 7.6.0、owlrl 7.6.1 は導入済み)、導入は依存追加を要する。

`warn` の扱いは V_req の設計で決める。既定では warn は pass と同値ではなく、π に「全ホップの再導出」を求める拒否理由として返す(rev5 の運用と同じ)。

## 12. 実験計画(提案のみ、未実行)

MetaQA[^metaqa] の開発用 600 問(rev5 と同一集合)で比較する。

**設計前に測れる数値(2026-09-21 実測)。** rev5 の記録 `results/metaqa-C1-rev5.jsonl`(600 件、実費 $38.3)で、verify-before-FINAL 契約の「呼ぶこと」の違反を数えた。

| 違反の種類 | 件数 |
|---|---|
| `kb_check_answer` を一度も呼ばずに答えた | 0 / 600 |
| `kb_verify` を一度も呼ばずに答えた | 1 / 600(1-hop、誤答) |
| 誤答 18 件のうち検査を呼んでいないもの | 0 |

つまり Sonnet 5 では、プロンプト契約の「呼ぶこと」の遵守率はすでに 1.0 であり、**ループ強制が「呼ばせる」ことで EM を上げる余地は MetaQA + Sonnet 5 にはない**。残る 18 件の誤答は、検査を呼んだ上での誤りである(検査した候補と答えた候補の不一致は記録に残っておらず、台帳があれば初めて数えられる)。これは Forced-CHECK [A13] の null 結果(検査の起動を強制しても利得なし)と同じ現象を、別の課題で観測したものと読める。

したがって実験は「rev5 を超えるか」ではなく、**「プロンプト契約なしでもループ強制だけで rev5 に届くか」「プロンプト遵守が崩れる条件で差が出るか」** を問う形にする。

| 条件 | プロンプト契約 | ループ強制 | 目的 | 費用の目安 |
|---|---|---|---|---|
| rev5(実測済み) | あり | なし | 上限の基準 0.970 | — |
| C1-rev2[^rev2](実測済み) | なし | なし | 下限の基準 0.790(同一 600 問) | — |
| GA∅ 義務型・契約なし | なし(下記の定義) | 台帳照合 | ループ強制単独で契約と同じ 0.970 に届くか、0.790 との差 +0.18 のどこまで回復するか | 約 $40 |
| GB∅ 自動型・契約なし | なし | 検証器も起動 | GA∅ との差 = 起動の決定論性 | 約 $40 |
| GA∅-haiku | なし | 台帳照合 | プロンプト遵守が弱いモデルで差が開くか(対照: rev5-haiku) | 約 $15 × 2 |

**「契約なし」の定義。** rev2 の素のプロンプトをそのまま使うと、答えの経路が「末尾に FINAL 行を書け」のままで submit の存在を π が知らず、全課題が Stop の後詰めに落ちて「拒否文から submit を発見する速さ」を測ることになる。これを避けるため、契約なし条件では QA_TASK の答えの経路を示す一文を「submit 道具を呼んで答えよ」に置き換え(または submit 道具の説明文にその旨を持たせ)、検証に関する文言(QA_VERIFY)は一切含めない。したがって GA∅ と rev5 の差は QA_VERIFY の有無だけであり、比較は関門の効果を分離する。GB∅ と GA∅-haiku も同じ定義に従う。

採点は、GA∅・GB∅ の各条件では正規表現ではなく、受理された submit の証明書に含まれる c を読む。証明書がない走行(拒否上限、手番上限、CLI の強制終了のいずれで終わっても)は unknown = 誤答として数える(fail-closed の費用を隠さない)。

仮説: (仮説1)GA∅ が rev5 に届けば、契約の効力はプロンプトの文言ではなく「検査結果に受理を条件付ける」という構造にある。0.790 と 0.970 の間に落ちれば、その位置がループ強制単独の寄与を与える。(仮説2)GA∅ ≈ GB∅ なら、利得の源泉は「検査の決定論性」であって「起動の決定論性」ではない。(仮説3)haiku では rev5-haiku < GA∅-haiku となり、ループ強制の価値はプロンプト遵守が崩れるところで現れる。(仮説4)拒否回数の分布はホップ[^hop]数とともに増え、fail-closed の unknown は 3-hop に集中する。

## 13. SDK での実現(設計ではなく実装対応)

導入済みの Claude Agent SDK 0.2.111 で、本設計は次の対応で実現できる(フック仕様は [D8])。

| 設計要素 | SDK の機構 | 備考 |
|---|---|---|
| submit 道具 | MCP[^mcp] 道具として定義 | 候補と証拠参照を構造化入力で受ける |
| 関門 G | PreToolUse[^pretool] フック(照合子[^matcher] = submit の道具名) | `tool_input` から候補を取り、台帳を照合。拒否は `hookSpecificOutput.permissionDecision: "deny"` + `permissionDecisionReason`(旧来の最上位 `decision` は本事象では非推奨) |
| 台帳への記帳 | PostToolUse[^posttool] フック(照合子 = 検証器の道具名) | 実行結果を台帳 L へ書く。GB 自動型では PreToolUse 内で検証器を直接呼ぶ |
| fail-closed 後詰め | Stop フック | 台帳の受理フラグが立っていなければ `decision: block` + `reason` で差し戻す。差し戻しは R に算入する |
| 委譲境界 | SubagentStop フック | 副エージェントの証明書を親台帳に転記 |
| GC 先制型 | `toggle_mcp_server` で submit 専用サーバーを有効化 | 台帳に pass が記帳された時点で submit サーバーを有効にし、候補が変わったら無効に戻す。ストリーミング接続時のみ動作 |

**より小さい実現(推奨)。** 記号道具の MCP サーバーは同一プロセス内にあるので、検証器の関数は台帳 L に直接記帳でき、submit 道具自身が台帳を照合して pass / reject を返せる。この形ではフックは Stop と SubagentStop の後詰めだけになり、PreToolUse での候補取り出しも PostToolUse での道具結果の解析も要らない。機構が小さいほど R3(分析と試験にかけられる)を満たしやすい。上の表の PreToolUse / PostToolUse 経路は、submit や検証器をプロセス外に置く場合の代替として残す。

注意点が四つある。第一に、Stop フックの入力は `transcript_path` と `stop_hook_active` だけで候補を持たないため、関門本体を Stop に置かない(§4)。第二に、Stop の差し戻し後に π が再び submit せず停止すれば Stop は再度呼ばれ(`stop_hook_active` が真)、8 回連続で CLI が強制終了する [D8]。関門は R ≤ 7 で自ら unknown に降着し、採点は証明書のみから行う(§12)。第三に、MCP 道具の名前は `mcp__symbolic__kb_check_answer` のように接頭辞付きで届くため、照合子はこの完全名で書く。第四に、PostToolUse の `decision: block` は「理由を道具結果の横に添える」だけで実行を取り消せない [D8] ので、台帳の記帳にのみ使い、拒否には使わない。

実装の前提条件を一つ挙げる。§8 規則 2 は「検証器は S_0 の複製上で閉包を計算し、実体化しない」を要求するが、現行の `kb.py` では π が呼んだ `kb_infer` が同一プロセス内のグラフに閉包を実体化し、その後の `kb_verify` と `check_answer` が同じ生きたグラフを読む(`check_answer` は `self.graph` を直接参照する。ディスクへの書き戻しは 2026-07-13 に修正済みだが、メモリ内の共有は残っている)。フック実装の最初の確認事項は、検証器の経路が生きているグラフではなく S_0 を読むことである。

## 14. 既存ソフトウェアにおける関門の層

「LLM の出力を機械が検査してから通す」機構はすでに多層に存在する。本設計の位置を定めるために整理する。

| 層 | 何を検査するか | 代表 | 本設計との関係 |
|---|---|---|---|
| 字句 | 出力が正規表現・文法に従うか(デコード時に有限状態機械で制約) | Outlines[^outlines] [A10] | 構文の保証のみ。意味の真偽は扱わない |
| 型 | 出力がスキーマに適合するか。不適合なら診断を添えて再生成 | Instructor[^instructor] [D6]、TypeChat[^typechat] [D7]、Guardrails AI[^guardrailsai] [D9] | 「拒否理由を添えて再試行」の先例。検査対象は形であって世界ではない |
| 流れ | 対話が定義済みの流れに従うか | NeMo Guardrails[^nemo] [A11](Colang) | 行為列の水準の関門。ECT [A14] と同じ層 |
| 意味 | 出力が知識基盤・仕様に照らして真か | **本設計**、LLM-Modulo [A7] | S_0 に対する閉世界検証 |

再試行の先例として DSPy Assertions[^dspy] [A9] がある。硬い Assert と柔らかい Suggest を区別し、失敗時はエラー文言と過去の試行を文脈に入れて再試行する。ただし検査の記述者はパイプラインの作者すなわち LLM を使う側であり、自己改善ループに置くと「自己著作の検証は当てにならない」(調査レビュー §3.5)問題に直面する。本設計の検証器は S_0 とともに π の手の届かない場所に置く点で異なる。

ハーネスのフック機構は各社にある。OpenAI Agents SDK[^oaisdk] の出力ガードレールは最終出力に対して走り、トリップワイヤーで例外を投げて実行を止める [D4](fail-closed だが再試行の経路はない)。Google ADK[^adk] の before_tool_callback は辞書を返すことで道具の実行を飛ばし、その辞書を結果として使う [D5](PreToolUse deny + 理由の挿入に相当)。LangGraph[^langgraph] は状態をグラフとして明示し、チェックポイントに永続化し、中断(interrupt)で人間の介入を待つ [D3b](S を T の外に置く先例)。Claude Code / Agent SDK のフック [D8] は §13 の通り。いずれも機構であって方針ではなく、「何を検証器にするか」「受理条件をどう書くか」は利用者に委ねられている。本設計はその方針の部分、すなわち知識基盤上の閉世界検証と候補束縛の台帳を与える。

運用系のアナロジーも記しておく。Kubernetes[^k8s] の検証用アドミッション・ウェブフック [D1] は、認証・認可の後、永続化の前に要求を横取りし、`allowed: false` と理由で拒否する。明示的な拒否は失敗時方針(failurePolicy)の設定に関わらず常に効く。GitHub の保護ブランチ [D2] は必須ステータスチェックが成功するまで変更を通さない。git の pre-commit [D3] は非零終了でコミットを中止するが `--no-verify` で迂回できる。関門を「迂回できない場所」に置くことの重要性(R1)は、これらの差に現れている。

## 15. 参考文献(書誌検証済み 2026-09-22)

### A. LLM エージェントの基礎

- [A1] Yao, S. et al. ReAct: Synergizing Reasoning and Acting in Language Models. ICLR 2023. arXiv:2210.03629.
- [A2] Schick, T. et al. Toolformer: Language Models Can Teach Themselves to Use Tools. NeurIPS 2023. arXiv:2302.04761.
- [A3] Karpas, E. et al. MRKL Systems: A modular, neuro-symbolic architecture that combines large language models, external knowledge sources and discrete reasoning. 2022. arXiv:2205.00445.
- [A4] Gao, L. et al. PAL: Program-aided Language Models. ICML 2023. arXiv:2211.10435.
- [A5] Shinn, N. et al. Reflexion: Language Agents with Verbal Reinforcement Learning. NeurIPS 2023. arXiv:2303.11366. 解説: `harness-resarch/articles/03-verification/reflexion.md`。
- [A6] Sumers, T. R. et al. Cognitive Architectures for Language Agents. TMLR 2024. arXiv:2309.02427.
- [A7] Kambhampati, S. et al. Position: LLMs Can't Plan, But Can Help Planning in LLM-Modulo Frameworks. ICML 2024. arXiv:2402.01817.
- [A8] (欠番: Tree of Thoughts は本文で引用しないため除外)
- [A9] Singhvi, A. et al. DSPy Assertions: Computational Constraints for Self-Refining Language Model Pipelines. 2023. arXiv:2312.13382.
- [A10] Willard, B. T. & Louf, R. Efficient Guided Generation for Large Language Models. 2023. arXiv:2307.09702(実装: Outlines)。
- [A11] Rebedea, T. et al. NeMo Guardrails: A Toolkit for Controllable and Safe LLM Applications with Programmable Rails. EMNLP 2023 Demo. arXiv:2310.10501.
- [A12] Huang, J. et al. Large Language Models Cannot Self-Correct Reasoning Yet. ICLR 2024. arXiv:2310.01798. 解説: `harness-resarch/articles/03-verification/cannot-self-correct.md`。
- [A13] Yi, H. & Song, X. Learning to Control LLM Agent Harnesses with Offline Reinforcement Learning. 2026. arXiv:2607.05458.
- [A14] When May an Agent Stop? Evidence-Carrying Termination for Tool-Using LLMs. 2026. arXiv:2608.23623.

### B. 古典的 AI・認知アーキテクチャ・知識システム

- [B1] Laird, J. E., Newell, A., Rosenbloom, P. S. SOAR: An architecture for general intelligence. Artificial Intelligence 33(1), 1987. DOI 10.1016/0004-3702(87)90050-6.
- [B2] Anderson, J. R. et al. An integrated theory of the mind. Psychological Review 111(4), 2004. DOI 10.1037/0033-295X.111.4.1036.
- [B3] Erman, L. D., Hayes-Roth, F., Lesser, V. R., Reddy, D. R. The Hearsay-II Speech-Understanding System. ACM Computing Surveys 12(2), 1980. DOI 10.1145/356810.356816.
- [B4] Rao, A. S. & Georgeff, M. P. BDI Agents: From Theory to Practice. ICMAS-95, 1995.
- [B5] Doyle, J. A truth maintenance system. Artificial Intelligence 12(3), 1979. DOI 10.1016/0004-3702(79)90008-0.
- [B5b] de Kleer, J. An assumption-based TMS. Artificial Intelligence 28(2), 1986. DOI 10.1016/0004-3702(86)90080-9.
- [B6] Reiter, R. On integrity constraints. TARK 1988, pp. 97–111.
- [B7] Knublauch, H. & Kontokostas, D. (eds.). Shapes Constraint Language (SHACL). W3C Recommendation, 2017-07-20. https://www.w3.org/TR/shacl/
- [B7b] Labra Gayo, J. E. et al. Validating RDF Data. Morgan & Claypool, 2018. DOI 10.2200/S00786ED1V01Y201707WBE016(§1.2 開世界/閉世界、§7.6 推論なしの検証)。
- [B8] Tao, J., Sirin, E., Bao, J., McGuinness, D. L. Integrity Constraints in OWL. AAAI 2010. DOI 10.1609/aaai.v24i1.7525.
- [B9] Patel-Schneider, P. F. Using Description Logics for RDF Constraint Checking and Closed-World Recognition. AAAI 2015. DOI 10.1609/aaai.v29i1.9177.
- [B10] Backus, J. Can programming be liberated from the von Neumann style? CACM 21(8), 1978. DOI 10.1145/359576.359579.

### C. セキュリティ・検証・制御の基礎

- [C1] Anderson, J. P. Computer Security Technology Planning Study. ESD-TR-73-51, 1972. Vol. I §3.2.2: 参照検証機構は「改竄不能でなければならない」「常に呼ばれなければならない」「分析と試験にかけられるほど小さくなければならない」(原文の第三要件は "verifiable" ではない)。
- [C2] Saltzer, J. H. & Schroeder, M. D. The protection of information in computer systems. Proc. IEEE 63(9), 1975. DOI 10.1109/PROC.1975.9939.
- [C3] Schneider, F. B. Enforceable security policies. ACM TISSEC 3(1), 2000. DOI 10.1145/353323.353382(実行監視で強制可能 ⇒ 安全性。逆は成り立たない、と本文 p. 35 に明記)。
- [C4] Ligatti, J., Bauer, L., Walker, D. Edit automata: enforcement mechanisms for run-time security policies. IJIS 4(1–2), 2005. DOI 10.1007/s10207-004-0046-8.
- [C5] Leucker, M. & Schallhart, C. A brief account of runtime verification. J. Log. Algebr. Program. 78(5), 2009. DOI 10.1016/j.jlap.2008.08.004.
- [C6] Necula, G. C. Proof-carrying code. POPL '97. DOI 10.1145/263699.263712.
- [C7] Sha, L. Using simplicity to control complexity. IEEE Software 18(4), 2001. DOI 10.1109/MS.2001.936213(Simplex の構造は二次資料経由で確認。要すれば Seto ら 1998 ACC を併記、未検証)。
- [C8] Alshiekh, M. et al. Safe Reinforcement Learning via Shielding. AAAI-18. DOI 10.1609/aaai.v32i1.11797(用語は preemptive / post-posed shielding)。
- [C9] Goldwasser, S., Micali, S., Rackoff, C. The knowledge complexity of interactive proof systems. SIAM J. Comput. 18(1), 1989. DOI 10.1137/0218012.
- [C10] Meyer, B. Applying "design by contract". Computer 25(10), 1992. DOI 10.1109/2.161279.
- [C11] Hoare, C. A. R. An axiomatic basis for computer programming. CACM 12(10), 1969. DOI 10.1145/363235.363259.

### D. ソフトウェア(文書は 2026-09-22 取得)

- [D1] Kubernetes. Dynamic Admission Control. https://kubernetes.io/docs/reference/access-authn-authz/extensible-admission-controllers/
- [D2] GitHub. About protected branches. https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches
- [D3] Git. githooks. https://git-scm.com/docs/githooks
- [D3b] LangGraph. Graph API / Persistence / Interrupts. https://docs.langchain.com/oss/python/langgraph/graph-api
- [D4] OpenAI Agents SDK. Guardrails. https://openai.github.io/openai-agents-python/guardrails/
- [D5] Google ADK. Types of Callbacks. https://adk.dev/callbacks/types-of-callbacks/
- [D6] Instructor. Retrying / Validation. https://python.useinstructor.com/concepts/retrying/
- [D7] Microsoft TypeChat. Introduction / FAQ. https://microsoft.github.io/TypeChat/docs/introduction/
- [D8] Claude Code. Hooks reference; Claude Agent SDK. Intercept and control agent behavior with hooks. https://code.claude.com/docs/en/hooks , https://code.claude.com/docs/en/agent-sdk/hooks
- [D9] Guardrails AI. Validator OnFail Actions. https://www.guardrailsai.com/docs/concepts/validator_on_fail_actions

---

## 脚注(凡例・注釈)

[^react]: ReAct — Reason + Act。思考と道具呼び出しを交互に行い、観測をトランスクリプトへ追記して繰り返すエージェントループの基本形 [A1]。
[^context]: context(文脈) — モデルが一度に読める入力全体。本書ではトランスクリプト T と同一視する。
[^gate]: 関門(ゲート) — 出力が必ず通過し、通過の可否を決定論的に決める検査点。
[^sdk]: SDK — Software Development Kit。ここでは Claude Agent SDK(Python)。
[^hook]: フック(hook) — SDK がループの特定時点(道具実行の前後、停止時)で呼ぶ利用者定義の処理。処理結果で続行・拒否・差し戻しを決められる。
[^harness]: ハーネス — モデル本体を除くエージェントの工学層(道具の配送、文脈管理、安全機構、サブエージェント編成、統制)。調査レポート §1 の定義に従う。
[^ltb]: 線形トランスクリプト・ボトルネック — 単一の線形テキストトランスクリプト + 同期的道具呼び出しを唯一の万能インターフェースとした設計判断。調査レポート §3 が六問題の共通根として同定した。
[^refmon]: 参照モニタ(reference monitor) — すべてのアクセスを仲介し可否を決める、計算機セキュリティの中核概念 [C1]。原文では reference validation mechanism。
[^safety]: 安全性(safety property) — 「悪いことが決して起きない」形の性質。有限の実行接頭辞で違反が確定する。
[^liveness]: 生存性(liveness property) — 「良いことがいつか起きる」形の性質。有限の接頭辞では違反を確定できない。
[^failclosed]: fail-closed — 判定できないときは「通さない」側に倒す方針。Saltzer & Schroeder [C2] の fail-safe defaults に対応。
[^cwa]: 閉世界仮定(CWA: Closed World Assumption) — 知識基盤に書かれていないことは偽と見なす立場。データベースと制約検証の前提。
[^owa]: 開世界仮定(OWA: Open World Assumption) — 書かれていないことは「不明」であり偽ではないとする立場。RDF・OWL の前提 [B9]。
[^ic]: 完全性制約(integrity constraint) — 知識基盤が満たすべき条件。Reiter [B6] は「世界についての言明ではなく、知識基盤が何を知っているかへの問い合わせ」と位置付けた。
[^shacl]: SHACL — Shapes Constraint Language。RDF グラフを「形状」の集合で検証する W3C 勧告 [B7]。
[^pcc]: 証明携行コード(PCC: Proof-Carrying Code) — 信頼できない生成者がコードに安全性の証明を添え、受け手が小さな検証器で証明だけを検査する方式 [C6]。
[^tcb]: 信頼計算基盤(TCB: Trusted Computing Base) — システムの安全性が依存する、信頼せざるを得ない構成要素の集合。小さいほどよい。
[^shield]: 遮蔽(shielding) — 学習方策の行為を監視し、仕様違反となる行為だけを訂正する決定論的な層 [C8]。
[^simplex]: Simplex アーキテクチャ — 高性能だが未検証の制御器と、単純で検証済みの基準制御器を並置し、安全包絡を越えそうなとき後者へ切り替える構成 [C7]。
[^tms]: 真理維持系(TMS: Truth Maintenance System) — 信念ごとにその理由(正当化)を記録し、前提が撤回されたとき依存する信念を機械的に撤回する仕組み [B5]。
[^atms]: ATMS — Assumption-based TMS。各信念を「どの仮定集合の下で成り立つか」で管理し、矛盾した情報の下でも文脈切替を安価にする [B5b]。
[^cli]: CLI — コマンドライン実行ファイル。ここでは Claude Code 本体。SDK は自身に同梱した版を優先して使う。
[^transcript]: トランスクリプト(transcript) — ユーザー入力、モデル出力、道具の入出力を時系列に並べた記録。
[^policy]: 方策(policy) — 状態から行為を選ぶ関数。ここではモデル本体 π。
[^modulo]: LLM-Modulo — Kambhampati ら [A7] の枠組み。LLM が候補を生成し、外部のモデルに基づく検証器が批評を返す双方向ループ。
[^c1]: C1 — nsai の実験条件。記号道具を任意で使える単一エージェント。
[^em]: EM — Exact Match。予測が正解と完全一致した割合。
[^rev5]: rev5 — プロンプト版 5。回答検証道具 `kb_check_answer` と verify-before-FINAL 契約をプロンプトで義務化した条件。論文の主結果(EM 0.970)。
[^rev2]: rev2 — プロンプト版 2。記号道具を任意の道具として与えるだけで、検証契約を持たない C1 の素の条件。MetaQA で EM 0.790。
[^kg]: 知識グラフ(KG) — RDF[^rdf] 三つ組で表した事実の集合。nsai では rdflib + owlrl。
[^rdf]: RDF — Resource Description Framework。「主語・述語・目的語」の三つ組で事実を表す W3C の規約。
[^owl]: OWL — Web Ontology Language。RDF 上で語彙の意味(クラス階層、関数プロパティ、同一性)を定義する W3C の規約。
[^prov]: 出典記録(provenance) — 三つ組ごとに「誰が・いつ・何を根拠に」を記録したもの。nsai では `kb.prov.jsonl`。
[^ledger]: 台帳(ledger) — 検証器の実行を一件ずつ記録した記号状態の一部。§5。
[^soar]: Soar — Laird・Newell・Rosenbloom の汎用認知アーキテクチャ [B1]。作業記憶と生成規則記憶を分け、行き詰まりから下位目標を生成する。
[^actr]: ACT-R — Anderson の認知アーキテクチャ [B2]。宣言的記憶モジュールと生成システムからなる。
[^gabc]: GA / GB / GC — 本書の施行変種の名前。GA 義務型(π が検証器を呼び、ループが台帳を照合)、GB 自動型(ループが検証器を起動)、GC 先制型(台帳に合格がない間は submit を提示しない)。§6。
[^hearsay]: Hearsay-II — 1970 年代の音声理解システム [B3]。黒板アーキテクチャの原型。
[^fc]: Forced-CHECK — Yi & Song [A13] の対照条件。学習制御器の代わりに check 行為を機械的に挿入する基線。
[^blackboard]: 黒板(blackboard) — 複数の独立した知識源が共有の構造化状態に読み書きし、制御部が次に動く知識源を選ぶアーキテクチャ [B3]。
[^bdi]: BDI — Belief-Desire-Intention。信念・願望・意図を分離して持つエージェントの枠組み [B4]。
[^coala]: CoALA — Cognitive Architectures for Language Agents [A6]。LLM エージェントを記憶・行為空間・意思決定手続きの三軸で整理する枠組み。
[^regex]: 正規表現(regex) — 文字列パターン照合。現行ハーネスは `FINAL:\s*(...)` で答えを抜く。
[^curie]: CURIE — Compact URI。`ns:Alice` のような接頭辞付きの短縮識別子。
[^stop]: Stop — SDK のフック事象。エージェントが「完了」として停止しようとする瞬間に呼ばれる。`decision: block` で差し戻せる。
[^cert]: 証明書(certificate) — 受理された答えに添付する、台帳項目と断面識別子の束。第三者が再計算できる。
[^ect]: ECT — Evidence-Carrying Termination [A14]。主張ごとに痕跡への束縛を持つ型付き証明書がなければ完了宣言(COMPLETE)を許さない手法。
[^owlrl]: OWL-RL — OWL 2 の規則ベース推論プロファイル。含意・矛盾判定に用いる。
[^editauto]: 編集オートマトン(edit automata) — 実行監視器の介入を「打ち切り・抑制・挿入」の組み合わせとして定式化したもの [C4]。
[^total]: 全域(total) — すべての入力に対して有限時間で値を返す性質。
[^sparql]: SPARQL — RDF グラフへの問い合わせ言語。
[^z3]: Z3 — Microsoft Research の SMT(充足可能性モジュロ理論)ソルバー。数値・論理主張の証明と反例生成に使う。
[^csp]: CSP — Constraint Satisfaction Problem(制約充足問題)。有限領域の割当・順序問題を厳密に解く。
[^rhb]: RHB — Reward Hacking Benchmark。道具使用エージェントの近道行動を測るベンチマーク。調査レビュー §3.3。
[^c2f]: C2f — nsai の実験条件。副エージェントへの委譲をプロンプトで強制した条件。自由文の受け渡しで答えが失われる不具合(51 件)を H1 構造化受け渡しで修正した。
[^h1]: H1 — [design-revision-plan.md](design-revision-plan.md) の修正案「構造化された委譲受け渡し」。副エージェントの FINAL 行を親が逐語で写す契約。仮説番号ではない。
[^substop]: SubagentStop — SDK のフック事象。副エージェントが停止しようとする瞬間に呼ばれる。
[^metaqa]: MetaQA — 映画知識グラフ上のマルチホップ問答ベンチマーク。nsai の主評価。
[^hop]: ホップ(hop) — 知識グラフ上で答えに至るまでに辿る辺の数。MetaQA は 1・2・3-hop の問題からなる。
[^mcp]: MCP — Model Context Protocol。道具をモデルへ提供するための規約。nsai の記号道具は同一プロセス内の MCP サーバーとして提供される。
[^pretool]: PreToolUse — SDK のフック事象。道具実行の直前に呼ばれ、`tool_name`・`tool_input` を受け取り、`permissionDecision` で allow / deny を返せる。
[^posttool]: PostToolUse — SDK のフック事象。道具実行の直後に呼ばれ、実行結果を受け取る。
[^matcher]: 照合子(matcher) — フックをどの道具名に対して発火させるかを指定する文字列。
[^outlines]: Outlines — 正規表現・文脈自由文法をデコード時の有限状態機械に変換し、出力の構造を保証するライブラリ [A10]。
[^instructor]: Instructor — Pydantic のスキーマで LLM 出力を検証し、失敗時は検証エラーを文脈に加えて再試行する Python ライブラリ [D6]。
[^typechat]: TypeChat — Microsoft のライブラリ。TypeScript の型で出力を検証し、不適合なら型検査の診断を添えて修復プロンプトを送る [D7]。
[^guardrailsai]: Guardrails AI — 検証器と失敗時動作(REASK / FIX / FILTER / REFRAIN / NOOP / EXCEPTION / FIX_REASK / CUSTOM)を組み合わせる出力検証ライブラリ [D9]。
[^nemo]: NeMo Guardrails — NVIDIA のツールキット。Colang で対話の流れを定義し、LLM に依存しない規則として施行する [A11]。
[^dspy]: DSPy Assertions — LLM パイプラインに計算的制約(硬い Assert、柔らかい Suggest)を書き、失敗時はエラー文言を添えて再試行する構成 [A9]。
[^oaisdk]: OpenAI Agents SDK — OpenAI のエージェント構築ライブラリ。出力ガードレールはトリップワイヤー例外で実行を止める [D4]。
[^adk]: ADK — Google の Agent Development Kit。道具実行前後のコールバックで実行の省略や結果の差し替えができる [D5]。
[^langgraph]: LangGraph — LangChain 社のライブラリ。エージェントを状態グラフとして定義し、チェックポイントに永続化し、interrupt で実行を中断できる [D3b]。
[^k8s]: Kubernetes — コンテナ統制基盤。アドミッション・ウェブフックは API 要求を永続化前に横取りして検証・拒否する [D1]。
