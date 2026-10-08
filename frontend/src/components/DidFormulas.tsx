import React from 'react';
import katex from 'katex';
import 'katex/dist/katex.min.css';

/**
 * "Mathematical Formulas Used" for the Legacy DiD Summary page. Every formula is taken from the code
 * that produced the figures on the page:
 *   src/features/build_causal_panel.py  (Stage 8 windows and ring outcomes)
 *   src/models/did_model.py             (Stage 9 paired difference = two-way FE)
 *   src/models/displacement_model.py    (Stage 11 direct / displacement / net, CR1 SEs)
 *   src/features/priority_score.py      (legacy Stage 12 use of tau_net)
 */

const tex = (src: string, displayMode: boolean) => katex.renderToString(src, { displayMode, throwOnError: false });

const M: React.FC<{ children: string }> = ({ children }) => (
  <span dangerouslySetInnerHTML={{ __html: tex(children, false) }} />
);

const Eq: React.FC<{ children: string; label?: string }> = ({ children, label }) => (
  <div className="my-3 overflow-x-auto rounded-card border border-line bg-line-soft/40 px-4 py-3">
    <div dangerouslySetInnerHTML={{ __html: tex(children, true) }} />
    {label && <p className="mt-1 text-center text-[11px] text-ink-soft">{label}</p>}
  </div>
);

const Code: React.FC<{ children: string }> = ({ children }) => (
  <code className="rounded bg-line-soft px-1 py-0.5 text-[12px]">{children}</code>
);

const H3: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <h3 className="pt-2 text-sm font-bold text-ink">{children}</h3>
);

const P: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <p className="text-[13px] leading-relaxed text-ink">{children}</p>
);

const SYMBOLS: [string, React.ReactNode][] = [
  ['p,\\; N', <>Matched pair (<Code>pair_id</Code>) and the number of pairs</>],
  ['r \\in \\{T, C\\}', <>Role: <M>T</M> = treatment site (the reported outage), <M>C</M> = its matched control site</>],
  ['i', <>Unit = one role of one pair (<Code>unit_id</Code>)</>],
  ['t \\in \\{pre,\\, during,\\, post\\}', <>Period window: pre <M>{'[\\text{created}-14\\text{ d},\\ \\text{created}]'}</M>, during <M>{'[\\text{created},\\ \\text{closed}]'}</M>, post <M>{'[\\text{closed},\\ \\text{closed}+14\\text{ d}]'}</M></>],
  ['Y_{p,r,t}', <>Crime count in the window: <Code>crime_100m</Code> (<M>{'d \\le 100'}</M> m) or <Code>crime_250m</Code> (<M>{'100 < d \\le 250'}</M> m)</>],
  ['\\Delta Y_{p,r}^{(t)}', <>Change in <M>Y</M> from the pre window to window <M>t</M></>],
  ['dd_p^{(t)}', <>Per-pair difference-in-differences (<Code>crime_100m_dd_post</Code>, <Code>crime_250m_dd_post</Code>, …)</>],
  ['\\hat{\\tau}^{(t)}', <>Estimated DiD effect: mean of <M>{'dd_p^{(t)}'}</M> over pairs</>],
  ['\\hat{\\tau}_{\\text{direct}},\\ \\hat{\\tau}_{\\text{displacement}},\\ \\hat{\\tau}_{\\text{net}}', <>Within 100 m, 100–250 m ring, and combined estimates (<Code>direct_post</Code>, <Code>displacement_post</Code>, <Code>net_post</Code>)</>],
  ['Treatment_i,\\ During_t,\\ Post_t', <>Indicators <Code>treatment</Code>, <Code>during</Code>, <Code>post_period</Code></>],
  ['\\mu_i,\\ \\lambda_t', <>Unit and period fixed effects</>],
  ['\\delta_D,\\ \\delta_P', <>Coefficients on <Code>treatment_x_during</Code> and <Code>treatment_x_post_period</Code></>],
  ['\\varepsilon_{it}', <>Error term</>],
  ['g,\\ G,\\ s_g', <>Cluster (<Code>treatment_h3_res7</Code>), number of clusters, cluster score sum</>],
  ['\\widehat{SE},\\ z_{0.975}', <>Cluster-robust standard error; normal critical value 1.95996</>],
  ['\\text{local\\_crime\\_rate}_j,\\ \\text{duration\\_factor}_j', <>Legacy Stage 12 inputs for outage <M>j</M>: pre-outage crimes per day within 250 m; outage duration in days</>],
];

export const DidFormulas: React.FC = () => (
  <section aria-labelledby="formulas" className="space-y-3">
    <h2 id="formulas" className="text-base font-bold text-ink">Mathematical Formulas Used</h2>
    <div className="card space-y-3 p-5">
      <H3>1. Difference-in-Differences Estimator</H3>
      <P>
        The estimator compares how crime changed around an outage with how it changed around a matched control location over the same windows.
        Subtracting the control change removes trends shared by both locations, so what remains is the change associated with the outage.
        Positive values mean more crime.
      </P>
      <Eq>{'\\text{DiD} = (\\text{Post} - \\text{Pre})_{T} - (\\text{Post} - \\text{Pre})_{C}'}</Eq>
      <P>The equivalent regression is a two-way fixed-effects model on the unit × period panel:</P>
      <Eq>{'Y_{it} = \\mu_i + \\lambda_t + \\delta_D\\,(Treatment_i \\times During_t) + \\delta_P\\,(Treatment_i \\times Post_t) + \\varepsilon_{it}'}</Eq>
      <P>
        It is estimated with the two-way within transformation <M>{'x_{it} - \\bar{x}_{i\\cdot} - \\bar{x}_{\\cdot t} + \\bar{x}_{\\cdot\\cdot}'}</M>.
        The unit effect <M>{'\\mu_i'}</M> absorbs the treatment main effect. The interaction coefficient <M>{'\\delta_P'}</M> is the DiD effect after the
        outage, and <M>{'\\delta_D'}</M> is the effect during it. In this balanced 1:1 panel, <M>{'\\hat{\\delta}'}</M> equals the mean paired difference in Section 3
        exactly; the pipeline checks this to within <M>{'10^{-9}'}</M>. Stage 9 also fits a pooled OLS with <Code>baseline_crime_intensity</Code> as a covariate,
        but it is not the source of the figures on this page.
      </P>

      <H3>2. Pre- and Post-Period Change</H3>
      <Eq>{'\\Delta Y_{p,r}^{(t)} = Y_{p,r,t} - Y_{p,r,pre}, \\qquad t \\in \\{during,\\ post\\}'}</Eq>
      <P>
        <M>{'Y_{p,r,t}'}</M> is the <Code>crime_100m</Code> or <Code>crime_250m</Code> count for role <M>r</M> of pair <M>p</M> in window <M>t</M>.
        The pre window is the 14 days up to the 311 <Code>created_date</Code>, during runs from <Code>created_date</Code> to <Code>closed_date</Code>, and post is the 14 days after <Code>closed_date</Code>.
        Outcomes are raw counts per window, so the during window varies in length with outage duration.
      </P>

      <H3>3. Treatment Effect</H3>
      <Eq>{'dd_p^{(t)} = (Y_{p,T,t} - Y_{p,T,pre}) - (Y_{p,C,t} - Y_{p,C,pre})'}</Eq>
      <Eq>{'\\hat{\\tau}^{(t)} = \\frac{1}{N} \\sum_{p=1}^{N} dd_p^{(t)}'}</Eq>
      <P>
        For each pair, the control's change is subtracted from the outage site's change. The estimated effect is the average over all pairs.
        Applied to each ring, this gives the three headline figures:
      </P>
      <Eq>{'\\hat{\\tau}_{\\text{direct}} = \\overline{dd}\\,[\\texttt{crime\\_100m}], \\qquad \\hat{\\tau}_{\\text{displacement}} = \\overline{dd}\\,[\\texttt{crime\\_250m}], \\qquad \\hat{\\tau}_{\\text{net}} = \\hat{\\tau}_{\\text{direct}} + \\hat{\\tau}_{\\text{displacement}}'}</Eq>
      <P>
        The rings are disjoint and together cover 0–250 m, so the net effect is their sum. The displacement ratio is a point estimate only, with no interval,
        and is undefined when <M>{'\\hat{\\tau}_{\\text{direct}} = 0'}</M>:
      </P>
      <Eq>{'\\text{Displacement ratio} = -\\,\\hat{\\tau}_{\\text{displacement}} \\,/\\, \\hat{\\tau}_{\\text{direct}}'}</Eq>
      <P>
        Uncertainty uses CR1 cluster-robust variance on <Code>treatment_h3_res7</Code>, with <M>{'d_p = (dd_p^{\\text{direct}},\\ dd_p^{\\text{displacement}})'}</M>:
      </P>
      <Eq>{'\\widehat{V} = \\frac{G}{G-1} \\cdot \\frac{1}{N^2} \\sum_{g=1}^{G} s_g s_g^{\\top}, \\qquad s_g = \\sum_{p \\in g} (d_p - \\bar{d})'}</Eq>
      <Eq>{'\\widehat{\\operatorname{Var}}(\\hat{\\tau}_{\\text{net}}) = \\widehat{V}_{11} + \\widehat{V}_{22} + 2\\,\\widehat{V}_{12}, \\qquad \\text{95\\% range} = \\hat{\\tau} \\pm z_{0.975}\\,\\widehat{SE}, \\qquad p = 2\\left[1 - \\Phi\\!\\left(|\\hat{\\tau}| / \\widehat{SE}\\right)\\right]'}</Eq>
      <P>An estimate is shown as distinguishable from zero when <M>{'p < 0.05'}</M>.</P>

      <H3>4. Crime Impact / Estimated Crimes Attributable to the Outage</H3>
      <P>
        Not computed. The implementation never converts <M>{'\\hat{\\tau}'}</M> into a number of crimes attributable to outages, so no formula is given here.
      </P>

      <H3>5. Repair-Prioritization Quantity</H3>
      <P>
        The legacy Stage 12 priority score uses <M>{'\\hat{\\tau}_{\\text{net}}'}</M> (<Code>net_post</Code>) as one global constant for every outage <M>j</M>:
      </P>
      <Eq>{'\\text{raw\\_priority}_j = \\max\\!\\left(0,\\ \\hat{\\tau}_{\\text{net}} \\cdot \\text{local\\_crime\\_rate}_j \\cdot \\text{duration\\_factor}_j\\right)'}</Eq>
      <Eq>{'\\text{local\\_crime\\_rate}_j = \\frac{\\#\\{\\text{crimes within 250 m in } [\\text{created}_j - 14\\text{ d},\\ \\text{created}_j)\\}}{14}, \\qquad \\text{duration\\_factor}_j = \\frac{\\text{outage\\_duration\\_hours}_j}{24}'}</Eq>
      <Eq>{'\\text{priority\\_score}_j = 100 \\cdot \\frac{\\text{raw}_j - \\min_k \\text{raw}_k}{\\max_k \\text{raw}_k - \\min_k \\text{raw}_k} \\quad (\\text{0 for all if } \\max = \\min)'}</Eq>
      <P>
        This is an index, not a count of crimes prevented. Because <M>{'\\hat{\\tau}_{\\text{net}}'}</M> is the same for every outage, min–max scaling cancels it.
        A positive value ranks outages by <M>{'\\text{local\\_crime\\_rate}_j \\times \\text{duration\\_factor}_j'}</M>, and a non-positive value gives every outage a score of 0.
        The legacy Stage 13 "expected crimes prevented" is the cumulative sum of <Code>priority_score</Code> over repaired outages, measured in index points, not crimes.
      </P>

      <H3>6. Variable Definitions</H3>
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Symbol</th>
              <th scope="col">Meaning</th>
            </tr>
          </thead>
          <tbody>
            {SYMBOLS.map(([sym, meaning]) => (
              <tr key={sym}>
                <td className="whitespace-nowrap"><M>{sym}</M></td>
                <td className="text-xs">{meaning}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  </section>
);
