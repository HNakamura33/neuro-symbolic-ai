"""maxFreeTime(eventTime, k, startTime, endTime) -> int

An event runs over [0, eventTime].  n non-overlapping meetings occupy
[startTime[i], endTime[i]], in order.  You may reschedule AT MOST k
meetings (keeping durations and relative order, staying inside the event)
to maximize the longest continuous free gap.  Return that maximum.
Equivalently: with gaps g0..gn (before the first meeting, between
consecutive meetings, after the last), the answer is the maximum sum of
k+1 CONSECUTIVE gaps.  1 <= k <= n.

Examples:
    maxFreeTime(5, 1, [1, 3], [2, 5]) == 2
    maxFreeTime(10, 1, [0, 2, 9], [1, 4, 10]) == 6
"""

def maxFreeTime(eventTime, k, startTime, endTime):
    gaps = [startTime[0]]
    for i in range(1, len(startTime)):
        gaps.append(startTime[i] - endTime[i - 1])
    gaps.append(eventTime - endTime[-1])
    window = k + 1
    current = sum(gaps[:window])
    best = current
    for i in range(window, len(gaps)):
        current += gaps[i] - gaps[i - window]
        if current > best:
            best = current
    return best
