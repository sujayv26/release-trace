FROM releasetrace-service:local

ARG VARIANT
ENV RELEASETRACE_TARGET_VARIANT=${VARIANT}
EXPOSE 8100
CMD ["sh", "-ec", "exec python -m demo.build_target \"$RELEASETRACE_TARGET_VARIANT\" --fixed --targets /etc/releasetrace/targets.json --host 0.0.0.0 --port 8100"]
