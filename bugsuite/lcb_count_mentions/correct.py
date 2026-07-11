"""countMentions(numberOfUsers, events) -> list

Users 0..numberOfUsers-1 are all online at time 0.  Each event is one of
  ["MESSAGE", "<timestamp>", mentions]  where mentions is "ALL" (count every
    user, online or not), "HERE" (count only users online at that
    timestamp), or a whitespace-separated list of tokens like "id3" (count
    those ids, duplicates count multiple times, offline users included);
  ["OFFLINE", "<timestamp>", "<id>"]  user <id> goes offline at <timestamp>
    and is automatically online again exactly 60 time units later (online
    again AT timestamp + 60).
Status changes at a timestamp are processed before messages at the same
timestamp.  Timestamps are non-negative integers given as strings.  Return
mentions-count per user id.

Example:
    countMentions(2, [["MESSAGE","10","id1 id0"], ["OFFLINE","11","0"],
                      ["MESSAGE","71","HERE"]]) == [2, 2]
"""

def countMentions(numberOfUsers, events):
    mentions = [0] * numberOfUsers
    offline_until = [0] * numberOfUsers

    def order(event):
        return (int(event[1]), 0 if event[0] == "OFFLINE" else 1)

    for kind, ts, payload in sorted(events, key=order):
        t = int(ts)
        if kind == "OFFLINE":
            offline_until[int(payload)] = t + 60
        elif payload == "ALL":
            for i in range(numberOfUsers):
                mentions[i] += 1
        elif payload == "HERE":
            for i in range(numberOfUsers):
                if offline_until[i] <= t:
                    mentions[i] += 1
        else:
            for token in payload.split():
                mentions[int(token[2:])] += 1
    return mentions
