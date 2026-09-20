*** Settings ***
Documentation     Federated read suite (EP-70, EP-71, PF-48).
...               Evaluates distributed operations over ContextSourceRegistrations: a hub space
...               that holds no entities of its own answers over the union of the spaces that do,
...               and says which of them an answer came from.
Resource          resources/ngsi_ld.resource
Suite Setup       Setup Federated Session
Suite Teardown    Teardown NGSI-LD Session
Force Tags        etsi    federated

*** Variables ***
# The two members of the hub under test, one type each, so a union is visible as a union.
${MEMBER_ONE_TYPE}      Vehicle
${MEMBER_TWO_TYPE}      KeyPerformanceIndicator
# The hub under test is a public open-data door, so the federated cases call it anonymously.
# The token the rest of the ETSI suites carry is minted for another endpoint's audience and is
# refused here with 401, which is the gateway being right rather than the hub being wrong.
${FEDERATED_TOKEN}      ${EMPTY}

*** Keywords ***
Setup Federated Session
    IF    '${FEDERATED_URL}' == '${EMPTY}'
        Skip    NGSILD_FEDERATED_URL is not set: skipping federation suite
    END
    Set Suite Variable    ${TOKEN}    ${FEDERATED_TOKEN}
    Open Custom NGSI-LD Session    federated_sut    ${FEDERATED_URL}

Query The Hub
    [Documentation]    One query on the hub, with the status left to the caller to assert.
    [Arguments]    ${type}    ${limit}=5
    ${response}=    Get From Custom Session    federated_sut    /entities
    ...    type=${type}    limit=${limit}
    RETURN    ${response}

*** Test Cases ***
EP-70 A Query On The Hub Answers Over The First Member
    [Documentation]    EP-70: an Endpoint on a space that holds ContextSourceRegistrations
    ...                answers a read over the union of the matching sources. The hub holds no
    ...                entity of its own, so anything that comes back came from a member.
    ${response}=    Query The Hub    ${MEMBER_ONE_TYPE}
    Should Be Equal As Integers    ${response.status_code}    200
    ${items}=    Set Variable    ${response.json()}
    ${len}=    Get Length    ${items}
    Should Be True    ${len} >= 1

EP-70 The Same Hub Answers Over The Second Member
    [Documentation]    EP-70: two registrations, two spaces, one endpoint. A hub that answered
    ...                only its first member would be a proxy, not a federation.
    ${response}=    Query The Hub    ${MEMBER_TWO_TYPE}
    Should Be Equal As Integers    ${response.status_code}    200
    ${items}=    Set Variable    ${response.json()}
    ${len}=    Get Length    ${items}
    Should Be True    ${len} >= 1

EP-70 A Partial Answer Is An Answer And Names What Did Not Reply
    [Documentation]    EP-70: a member that is down, slow or refusing must not turn the whole
    ...                question into an error. A healthy federation answers 200; when one member
    ...                does not reply the answer is 207 with NGSILD-Warning naming it, and the
    ...                body still carries what did. Both are asserted, because which one happens
    ...                is the cluster's business and neither may be an error.
    ${response}=    Query The Hub    ${MEMBER_ONE_TYPE}
    Should Contain    ${{ [200, 207] }}    ${response.status_code}
    IF    ${response.status_code} == 207
        Dictionary Should Contain Key    ${response.headers}    NGSILD-Warning
        ${warning}=    Set Variable    ${response.headers}[NGSILD-Warning]
        Should Contain    ${warning}    urn:ngsi-ld:ContextSourceRegistration:
        Should Not Contain    ${warning}    http://
        Should Not Contain    ${warning}    https://
    END
    ${items}=    Set Variable    ${response.json()}
    ${len}=    Get Length    ${items}
    Should Be True    ${len} >= 1

EP-71 A Federated Answer Carries No Member Address
    [Documentation]    EP-71: a member is named by its registration and never by its URL. A hub
    ...                answer that leaked a member's internal address would tell a public caller
    ...                where to knock next.
    ${response}=    Get From Custom Session    federated_sut    /entities
    ...    type=${MEMBER_ONE_TYPE}    limit=5    options=sysAttrs
    Should Contain    ${{ [200, 207] }}    ${response.status_code}
    ${body}=    Set Variable    ${response.text}
    Should Not Contain    ${body}    .svc.cluster.local
    Should Not Contain    ${body}    context-broker

PF-48 The Hub's Own Policy Set Is The Only Gate
    [Documentation]    PF-48: the hub's broker reads its members' tenants directly, so what a
    ...                member's own Endpoint grants does not travel with the answer. A type the
    ...                hub does not grant is absent whatever a member would have served, and an
    ...                operation it does not grant is refused rather than forwarded.
    ${ungranted}=    Query The Hub    Event
    Should Be Equal As Integers    ${ungranted.status_code}    200
    ${items}=    Set Variable    ${ungranted.json()}
    Should Be Empty    ${items}
    # `retrieveOps` is retrieveEntity and queryEntity (CIM 009 Table 4.20-2); the type list and
    # the temporal history are other operations, and this hub grants neither.
    ${types}=    Get From Custom Session    federated_sut    /types
    Should Be Equal As Integers    ${types.status_code}    403
    ${temporal}=    Get From Custom Session    federated_sut    /temporal/entities
    ...    type=${MEMBER_ONE_TYPE}
    Should Be Equal As Integers    ${temporal.status_code}    403
