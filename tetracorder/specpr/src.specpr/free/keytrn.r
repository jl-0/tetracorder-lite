	subroutine keytrn (keycon)
	implicit integer*4 (i-n)

#ccc  name: keytrn.r
#ccc  version date: 7-24-85
#ccc  author(s): Kathy Kierein
#ccc  language: ratfor
#ccc
#ccc  short description: This subroutine is used in specpr to
#ccc 		         translate alias words and substitute this 
#ccc                     translation into the line.
#ccc
#ccc  algorithm description: none
#ccc  system requirements: none
#ccc  subroutines called: getkey.r, allist.r
#ccc  argument list description:
#ccc  parameter description:
#ccc  common description:
#ccc  message files referenced:
#ccc  internal variables: temp-temporary storage of iopcon while
#ccc				translation takes place
#ccc  file description:
#ccc  user command lines:
#ccc  update information:
#ccc  NOTES:
#ccc
# This subroutine translates the alias and substitutes the
# translation into the line

	include "../common/spmaxes"   # max parameters, must be first

	include "../common/key1"
	include "../common/lbl4"
	include "../common/lundefs"
	include "../common/iocontrol"

	character*200 temp
	logical keycon

	integer*4  bt,bt2,bt3

	character*1 ihbcksl

	ihbcksl = char(92)  # this is the backslash character

	ibeg = 1
	bt = 0
	beg = 0
	nend = 0


	if (numals == 0) {
		go to 50
	}

	if (iopcon(1:2)== ihbcksl // '#') {   # entire line is a comment, so do nothing

		return
	}

# This finds the alias and checks the boundaries


	while (beg <= nend) { 

	call getkey

	if (keysiz == 0) return

	if (beg < 1) beg = 1
	if (lnend < 1) lnend = 1
	if (lnend >= maxcline) lnend = maxcline - 1
	if (beg >= nend) {
		go to 50
	}


# This searches memory for a match to the alias and substitutes
# the correct translation into the line

	do j=1,numals {

		if (keysiz == alsize(j)) {
			if (ikeywd(1:keysiz) == cmdals(j)(1:alsize(j))) {

				bt = beg + trnsiz(j)

				if (bt > maxcline) {
					if (ioutverbose <= 1) {
						write (ttyout,10) bt
10						format('Translation excedes length of line, new length=',i6)
						write (ttyout,11) beg,  trnsiz(j), bt
11						format('            begin at ', i6,' variable value length= ',i6' end: ',i6)
						write (ttyout,12) j, cmdals(j)(1:alsize(j)), cmdtrn(j)(1:trnsiz(j))
12						format('            variable ',i3,': ', a,' = ',a)
					}
					bt = maxcline
				}

				temp (1:maxcline) = iopcon (lnend+1:maxcline) 
				iopcon (beg:maxcline) = ' '

				iend = trnsiz(j)
				if (iend > 75) iend = 75
				if (iend <  1) iend =  1
				if (trnsiz(j) > 1) {  # strip comments
					do ii = 1, trnsiz(j)-1 {
						if (cmdtrn(j)(ii:ii+1)== ihbcksl // '#') {
							iend = ii -1

							# find new last non blank
							i2 = lnb (cmdtrn(j)(1:iend))
							iend = i2
							if (iend < 1) iend = 1
							break
						}
					}
				}

				bt2 = beg+iend-1
				if (bt2 > maxcline - 1) bt2 = maxcline - 1
				if (bt2 <  1) bt2 =  1
				if (beg > bt2) beg = bt2
				#write(ttyout,*) 'DEBUG: beg,bt2, j, iend=',beg,bt2,j,iend
				iopcon (beg:bt2) = cmdtrn(j) (1:iend) 
				bt3 = maxcline-bt2
				if (bt3 < 1) bt3 = 1
				iopcon (bt2+1:maxcline) = temp (1:bt3)

				

				keycon = .true.
				if (ioutverbose == 0) {
					write(ttyout,30) iopcon
30					format(a)
				}
				go to 100
			}
		}
	}

	ibeg = beg + 1
	if (iopcon(beg:beg) == '[') {
		write (ttyout,20)
20		format ('Error, alias is not on list.')
		call what (-1)
 	} 

100	dummy = 1	
	}
50	return
	end
